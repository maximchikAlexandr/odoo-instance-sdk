"""Template clone planning, filestore containment, and fail-closed sharing."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import ConfigError, DatabaseAlreadyExistsError
from odoo_instance_sdk.internal.proc import PreparedStep, ProcessResult
from odoo_instance_sdk.models import DatabaseCopyStrategy, EnvironmentDatabaseMode
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.environment.checkout_artifacts import _row_to_env
from odoo_instance_sdk.resources.environment.checkout_planning import (
    EnvironmentCheckoutOptions,
    _CheckoutPlan,
)
from odoo_instance_sdk.resources.environment.checkout_template import (
    consume_template_preflight,
    database_copy_steps,
    publish_isolated_filestore,
    reject_shared_filestore_mode,
    remove_recorded_filestore,
    rollback_template_checkout,
    run_template_clone,
    template_cleanup_plan,
    template_database_drop_command,
    validate_copy_strategy,
)
from odoo_instance_sdk.storage.backup_catalog import CopyJournalStage
from tests.unit.monitor_support import make_catalog, make_env, seed_env


def _result(stdout: str, returncode: int = 0) -> ProcessResult:
    return ProcessResult(
        argv=(),
        returncode=returncode,
        stdout=stdout,
        stderr="",
        duration=0.0,
        cwd=None,
        environment=(),
    )


class _Scripted:
    def __init__(self, results: dict[str, ProcessResult]) -> None:
        self._results = results
        self.skipped: list[str] = []

    def process(self, step_id: str) -> ProcessResult:
        return self._results[step_id]

    def planned(self, step_id: str) -> bool:
        return step_id in self._results

    def skip(self, step_id: str) -> None:
        self.skipped.append(step_id)

    def consumed(self, step_id: str) -> bool:
        return step_id in self.skipped

    def action(self, step_id: str) -> None:
        return None

    def complete_action(self, step_id: str) -> None:
        return None


def _plan(tmp_path: Path) -> _CheckoutPlan:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    data = tmp_path / "data"
    source = data / "filestore" / "alpha"
    source.mkdir(parents=True)
    (source / "doc.txt").write_bytes(b"file")
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\ndb_host = localhost\ndb_name = alpha\n")
    return _CheckoutPlan(
        project=ProjectConfig(repository_root=root),
        env_id=uuid.uuid4(),
        name="env",
        repo_root=root,
        git_common_dir=str(root / ".git"),
        branch="feature",
        base_ref="main",
        base_revision="a" * 40,
        worktree=tmp_path / "wt",
        venv=tmp_path / "venv",
        generated_config=tmp_path / "generated.conf",
        dependency_lock=tmp_path / "lock.txt",
        env_root=tmp_path / "env",
        python_path="",
        python_owned=False,
        python_selector=None,
        http_interface="127.0.0.1",
        http_port=8069,
        db_mode=EnvironmentDatabaseMode.COPY,
        source_database="alpha",
        target_database="beta",
        source_config=conf,
        config_values={"db_host": "localhost", "db_port": "5432", "db_user": "odoo"},
        odoo_bin=str(tmp_path / "odoo-bin"),
        runtime_cwd=str(root),
        dependency_inputs=(),
        hash_lock=None,
        worktree_argv=(),
        created_at="2026-11-16T00:00:00+00:00",
        options=EnvironmentCheckoutOptions(
            db_mode=EnvironmentDatabaseMode.COPY,
            db_copy_strategy=DatabaseCopyStrategy.TEMPLATE,
            source_database="alpha",
            target_database="beta",
        ),
        data_dir=data,
    )


def _options(**overrides: object) -> EnvironmentCheckoutOptions:
    values: dict[str, object] = {
        "db_mode": EnvironmentDatabaseMode.COPY,
        "db_copy_strategy": DatabaseCopyStrategy.TEMPLATE,
        "source_database": "alpha",
        "target_database": "beta",
    }
    values.update(overrides)
    return EnvironmentCheckoutOptions(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("host", "overrides"),
    [
        ("localhost", {}),
        ("127.0.0.1", {}),
        ("::1", {}),
        ("", {}),
        (None, {}),
        ("LocalHost", {}),
    ],
)
def test_template_strategy_accepts_local_copy_hosts(
    host: str | None, overrides: dict[str, object]
) -> None:
    validate_copy_strategy(_options(**overrides), host)


@pytest.mark.parametrize(
    ("host", "overrides", "match"),
    [
        ("db.example", {}, "local PostgreSQL"),
        ("localhost", {"db_mode": EnvironmentDatabaseMode.SHARED}, "copy mode"),
        ("localhost", {"remote_name": "prod"}, "remote"),
        ("localhost", {"backup_id": "backup"}, "backup"),
    ],
)
def test_template_strategy_rejects_remote_shared_and_backup_sources(
    host: str, overrides: dict[str, object], match: str
) -> None:
    with pytest.raises(ConfigError, match=match):
        validate_copy_strategy(_options(**overrides), host)


def test_backup_strategy_is_accepted_for_any_host() -> None:
    validate_copy_strategy(
        EnvironmentCheckoutOptions(db_copy_strategy=DatabaseCopyStrategy.BACKUP),
        "db.example",
    )


def test_shared_filestore_request_is_rejected_and_has_no_pool_api() -> None:
    reject_shared_filestore_mode(None)
    reject_shared_filestore_mode("isolated")
    with pytest.raises(ConfigError, match="shared filestore mode is not available"):
        reject_shared_filestore_mode("shared")
    with pytest.raises(TypeError):
        EnvironmentCheckoutOptions(filestore_mode="shared")  # type: ignore[call-arg]


def test_checkout_help_defaults_db_copy_strategy_to_backup() -> None:
    result = CliRunner().invoke(cli, ["env", "checkout", "--help"])
    assert result.exit_code == 0
    assert "--db-copy-strategy" in result.output
    assert "[backup|template]" in result.output
    assert "backup" in result.output


def test_template_steps_bind_names_and_do_not_mutate(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    target = plan.data_dir / "filestore" / "beta"  # type: ignore[operator]
    steps = database_copy_steps(plan)
    create = next(step for step in steps if step.step_id == "checkout.template.create")
    preflight = next(step for step in steps if step.step_id == "checkout.template.preflight")
    assert isinstance(create, PreparedStep)
    assert isinstance(preflight, PreparedStep)
    assert "source_db=alpha" in create.argv
    assert "target_db=beta" in create.argv
    sql = (create.stdin or b"").decode()
    assert "format('CREATE DATABASE %I WITH TEMPLATE %I'" in sql
    assert "alpha" not in sql
    assert "beta" not in sql
    assert "pg_terminate_backend" not in (preflight.argv[-1] if preflight.argv else "")
    assert "pg_terminate_backend" not in sql
    assert not target.exists()
    drop = next(step for step in steps if step.step_id == "checkout.template.drop")
    assert isinstance(drop, PreparedStep)
    assert create.timeout is None
    assert drop.timeout is None
    assert b"pg_terminate_backend" in (drop.stdin or b"")


def test_quoted_database_name_is_rejected_before_clone_steps(tmp_path: Path) -> None:
    plan = replace(_plan(tmp_path), source_database="a'b")
    with pytest.raises(ConfigError, match="Invalid database name"):
        database_copy_steps(plan)


def test_backup_steps_stay_on_the_restore_probe_path(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan = replace(
        plan,
        options=EnvironmentCheckoutOptions(
            db_mode=EnvironmentDatabaseMode.COPY,
            db_copy_strategy=DatabaseCopyStrategy.BACKUP,
            source_database="alpha",
            target_database="beta",
        ),
    )
    identifiers = [step.step_id for step in database_copy_steps(plan)]
    assert "database.restore.exists-before" in identifiers
    assert "checkout.template.create" not in identifiers


def test_filestore_copy_is_isolated_and_rejects_symlinks(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    data = plan.data_dir
    assert data is not None
    source = data / "filestore" / "alpha" / "doc.txt"
    target, warning = publish_isolated_filestore(data, "alpha", "beta", plan.env_id)
    assert warning is None
    copied = target / "doc.txt"
    assert copied.read_bytes() == b"file"
    assert os.stat(source).st_ino != os.stat(copied).st_ino
    assert os.stat(copied).st_nlink == 1
    (data / "filestore" / "alpha" / "link").symlink_to(source)
    with pytest.raises(ConfigError, match="symlink"):
        publish_isolated_filestore(data, "alpha", "gamma", uuid.uuid4())


def test_missing_filestore_warns_without_creating_a_target(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "filestore").mkdir(parents=True)
    target, warning = publish_isolated_filestore(data, "alpha", "beta", uuid.uuid4())
    assert warning is not None
    assert "absent" in warning
    assert not target.exists()


def test_recorded_filestore_removal_requires_the_owned_path(tmp_path: Path) -> None:
    data = tmp_path / "data"
    owned = data / "filestore" / "beta"
    owned.mkdir(parents=True)
    (owned / "doc.txt").write_bytes(b"file")
    with pytest.raises(ConfigError, match="not the owned template target"):
        remove_recorded_filestore(str(data / "filestore" / "alpha"), data, "beta")
    assert owned.exists()
    remove_recorded_filestore(str(owned), data, "beta")
    assert not owned.exists()


def test_active_sessions_and_existing_target_stop_before_clone(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    busy = _Scripted({"checkout.template.preflight": _result("1|1|0|999")})
    with pytest.raises(ConfigError, match="active sessions"):
        consume_template_preflight(busy, plan)  # type: ignore[arg-type]
    present = _Scripted({"checkout.template.preflight": _result("0|1|1|999")})
    with pytest.raises(DatabaseAlreadyExistsError):
        consume_template_preflight(present, plan)  # type: ignore[arg-type]


def test_marker_failure_stays_pending_and_success_records_no_backup(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    catalog = make_catalog(tmp_path)
    recorded = str(plan.data_dir / "filestore" / "beta")  # type: ignore[operator]
    seed_env(
        catalog,
        make_env(
            str(plan.env_id),
            db_mode="copy",
            db_copy_strategy="template",
            source_db_name="alpha",
            target_db_name="beta",
            state="creating",
        ),
    )
    pending = _Scripted(
        {
            "checkout.template.create": _result(""),
            "checkout.template.verify": _result("0|1|1|999"),
            "checkout.template.connect": _result("1"),
            "checkout.template.neutralize": _result(""),
            "checkout.template.marker": _result("false"),
        }
    )
    with pytest.raises(ConfigError, match="neutralization marker"):
        run_template_clone(pending, catalog, plan, cluster_identity="999")  # type: ignore[arg-type]
    journal = catalog.get_copy_journal(str(plan.env_id))
    assert journal is not None
    assert journal["stage"] == CopyJournalStage.RESTORE_PENDING.value
    assert journal["backup_id"] is None

    plan = _plan(tmp_path / "ready")
    ready_catalog = tmp_path / "ready-catalog"
    ready_catalog.mkdir()
    catalog = make_catalog(ready_catalog)
    recorded = str(plan.data_dir / "filestore" / "beta")  # type: ignore[operator]
    seed_env(
        catalog,
        make_env(
            str(plan.env_id),
            db_mode="copy",
            db_copy_strategy="template",
            source_db_name="alpha",
            target_db_name="beta",
            state="creating",
        ),
    )
    ready = _Scripted(
        {
            "checkout.template.create": _result(""),
            "checkout.template.verify": _result("0|1|1|999"),
            "checkout.template.connect": _result("1"),
            "checkout.template.neutralize": _result(""),
            "checkout.template.marker": _result("true\n"),
            "checkout.template.drop": _result(""),
        }
    )
    run_template_clone(ready, catalog, plan, cluster_identity="999")  # type: ignore[arg-type]
    journal = catalog.get_copy_journal(str(plan.env_id))
    assert journal is not None
    assert journal["stage"] == CopyJournalStage.RESTORED.value
    assert journal["backup_id"] is None
    assert ready.skipped == []
    row = catalog.get_environment(str(plan.env_id))
    assert row is not None
    env = _row_to_env(row)
    assert env.db_copy_strategy is DatabaseCopyStrategy.TEMPLATE
    assert env.target_filestore_path == recorded
    assert env.backup_id is None
    cleanup = template_cleanup_plan(env, None, journal)  # type: ignore[arg-type]
    assert cleanup.backup_id is None
    assert cleanup.stage is CopyJournalStage.RESTORED
    failed = rollback_template_checkout(catalog, plan.env_id, context=ready)  # type: ignore[arg-type]
    assert failed is False
    assert not Path(recorded).exists()


def test_template_drop_command_terminates_only_when_forced(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    config = plan.generated_config
    config.write_text(
        "[options]\ndb_host = 127.0.0.1\ndb_port = 5432\ndb_user = odoo\nadmin_passwd = secret\n"
    )
    env = _row_to_env(
        _environment_row(
            tmp_path,
            plan,
            generated_config_path=str(config),
        )
    )
    plain = template_database_drop_command(
        env, executor=None, force_connections=False, step_prefix=""
    )
    assert plain is not None
    assert [step.step_id for step in plain.steps] == ["checkout.template.remove.drop"]
    drop = plain.steps[0]
    assert isinstance(drop, PreparedStep)
    assert "target_db=beta" in drop.argv
    assert "beta" not in (drop.stdin or b"").decode()
    forced = template_database_drop_command(
        env, executor=None, force_connections=True, step_prefix=""
    )
    assert forced is not None
    assert [step.step_id for step in forced.steps] == [
        "checkout.template.remove.terminate",
        "checkout.template.remove.drop",
    ]


def test_rollback_keeps_filestore_until_the_owned_database_drops(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    catalog = make_catalog(tmp_path)
    existing = plan.data_dir / "filestore" / "beta"  # type: ignore[operator]
    existing.mkdir()
    (existing / "keep.txt").write_bytes(b"keep")
    seed_env(
        catalog,
        make_env(
            str(plan.env_id),
            db_mode="copy",
            db_copy_strategy="template",
            target_db_name="beta",
            state="creating",
        ),
    )
    catalog.upsert_copy_journal(
        str(plan.env_id),
        target_database="beta",
        db_host="localhost",
        db_port=5432,
        db_user="odoo",
        backup_id=None,
        stage=CopyJournalStage.RESTORE_PENDING,
    )
    opened = _Scripted({"checkout.template.drop": _result("")})
    assert rollback_template_checkout(catalog, plan.env_id, context=opened) is False  # type: ignore[arg-type]
    assert (existing / "keep.txt").read_bytes() == b"keep"
    catalog.update_environment(str(plan.env_id), {"target_filestore_path": str(existing)})
    blocked = _Scripted({"checkout.template.drop": _result("", returncode=1)})
    assert rollback_template_checkout(catalog, plan.env_id, context=blocked) is True  # type: ignore[arg-type]
    assert (existing / "keep.txt").read_bytes() == b"keep"


def _environment_row(tmp_path: Path, plan: _CheckoutPlan, **overrides: str) -> sqlite3.Row:
    catalog_dir = tmp_path / "drop-catalog"
    catalog_dir.mkdir()
    catalog = make_catalog(catalog_dir)
    values = make_env(
        str(plan.env_id),
        db_mode="copy",
        db_copy_strategy="template",
        source_db_name="alpha",
        target_db_name="beta",
        target_filestore_path=str(plan.data_dir / "filestore" / "beta"),  # type: ignore[operator]
        generated_config_path=str(plan.generated_config),
    )
    values.update(overrides)
    seed_env(catalog, values)
    row = catalog.get_environment(str(plan.env_id))
    assert row is not None
    return row
