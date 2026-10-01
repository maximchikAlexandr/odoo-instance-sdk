from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from alembic import command
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.internal.dbprep.bootstrap import (
    BootstrapFailedError,
    BootstrapOutcome,
    _record_bootstrap_event,
    bootstrap_record_action,
    bootstrap_tmp_steps,
    ensure_project_bootstrap_tmp,
    run_bootstrap_tmp,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedProcess,
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    RunContext,
)
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from odoo_instance_sdk.storage.catalog.helpers import PostgresClusterClaim
from odoo_instance_sdk.storage.catalog_migrate import _alembic_config


def _active_claim(catalog: BackupCatalog, *, project: str = "project") -> PostgresClusterClaim:
    claim = catalog._ensure_postgres_cluster_pending(project, "compose", f"volume-{project}")
    return catalog._activate_postgres_cluster(
        claim.cluster_id,
        project,
        "compose",
        f"volume-{project}",
    )


def _process_steps() -> tuple[PreparedStep, PreparedStep, PreparedStep]:
    return (
        PreparedStep(step_id="spawn", argv=("odoo",), mutating=True),
        PreparedStep(step_id="probe", argv=("psql",), read_only=True),
        PreparedStep(step_id="ready", argv=("psql",), read_only=True),
    )


def _bootstrap_context(
    *,
    probe_stdout: str = "uninstalled",
    ready_stdout: str = "installed",
    include_actions: bool = False,
) -> tuple[RunContext[object], RecordingExecutor, tuple[PreparedStep, PreparedStep, PreparedStep]]:
    process_steps = _process_steps()
    verify = PreparedAction(step_id="verify", action="verify-bootstrap-tmp-base", read_only=True)
    record = bootstrap_record_action()
    steps: tuple[PreparedStep | PreparedAction, ...] = (
        *process_steps,
        *((verify, record) if include_actions else ()),
    )

    def result_factory(step: PreparedProcess) -> ProcessResult:
        prepared = cast("PreparedStep", step)
        stdout = ""
        if prepared.step_id == "probe":
            stdout = probe_stdout
        elif prepared.step_id == "ready":
            stdout = ready_stdout
        return ProcessResult(
            argv=prepared.argv,
            returncode=0,
            stdout=stdout,
            stderr="",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    executor = RecordingExecutor(result_factory=result_factory)
    return RunContext(steps, executor), executor, process_steps


def _catalog_with_active_claim(
    tmp_path: Path, *, project: str = "project"
) -> tuple[BackupCatalog, PostgresClusterClaim]:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    return catalog, _active_claim(catalog, project=project)


def _bootstrap_data_directory(tmp_path: Path) -> Path:
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    return data_directory


def test_catalog_bootstrap_writer_records_exact_event_fields(tmp_path: Path) -> None:
    catalog, claim = _catalog_with_active_claim(tmp_path)
    data_directory = _bootstrap_data_directory(tmp_path)
    try:
        catalog._record_database_bootstrapped(
            "localhost",
            5432,
            "tmp",
            cluster_id=claim.cluster_id,
            data_directory=data_directory,
        )
        event = catalog._latest_database_event("localhost", 5432, "tmp")
        assert event is not None
        assert event["event_type"] == "bootstrapped"
        assert event["cluster_id"] == str(claim.cluster_id)
        assert event["data_directory"] == str(data_directory)
        assert event["backup_id"] is None
        assert event["source_kind"] is None
        assert event["source_sha256"] is None
    finally:
        catalog.close()


def test_catalog_bootstrap_writer_orders_drop_after_bootstrap(tmp_path: Path) -> None:
    catalog, claim = _catalog_with_active_claim(tmp_path)
    data_directory = _bootstrap_data_directory(tmp_path)
    try:
        catalog._record_database_bootstrapped(
            "localhost",
            5432,
            "tmp",
            cluster_id=claim.cluster_id,
            data_directory=data_directory,
        )
        first = catalog._latest_database_event("localhost", 5432, "tmp")
        assert first is not None
        catalog.record_database_dropped("localhost", 5432, "tmp")
        latest = catalog._latest_database_event("localhost", 5432, "tmp")
        assert latest is not None
        assert latest["event_type"] == "dropped"
        assert int(latest["sequence"]) > int(first["sequence"])
    finally:
        catalog.close()


def test_catalog_bootstrap_writer_rejects_invalid_payloads(tmp_path: Path) -> None:
    catalog, claim = _catalog_with_active_claim(tmp_path)
    data_directory = _bootstrap_data_directory(tmp_path)
    try:
        catalog._record_database_bootstrapped(
            "localhost",
            5432,
            "tmp",
            cluster_id=claim.cluster_id,
            data_directory=data_directory,
        )
        with pytest.raises(BackupCatalogError, match="database tmp"):
            catalog._record_database_bootstrapped(
                "localhost",
                5432,
                "legacy",
                cluster_id=claim.cluster_id,
                data_directory=data_directory,
            )
        with pytest.raises(BackupCatalogError, match="data directory"):
            catalog._record_database_bootstrapped(
                "localhost",
                5432,
                "tmp",
                cluster_id=claim.cluster_id,
                data_directory="  ",
            )
        assert (
            catalog._conn.execute(
                "SELECT COUNT(*) FROM database_events WHERE event_type='bootstrapped'"
            ).fetchone()[0]
            == 1
        )
    finally:
        catalog.close()


def test_catalog_bootstrap_writer_requires_active_claim(tmp_path: Path) -> None:
    catalog, _claim = _catalog_with_active_claim(tmp_path)
    data_directory = _bootstrap_data_directory(tmp_path)
    try:
        pending = _active_claim(catalog, project="pending")
        catalog._conn.execute(
            "UPDATE postgres_clusters SET state='pending' WHERE cluster_id=?",
            (str(pending.cluster_id),),
        )
        catalog._conn.commit()
        with pytest.raises(BackupCatalogError, match="active cluster claim"):
            catalog._record_database_bootstrapped(
                "localhost",
                5432,
                "tmp",
                cluster_id=pending.cluster_id,
                data_directory=data_directory,
            )
        assert (
            catalog._conn.execute(
                "SELECT COUNT(*) FROM database_events WHERE event_type='bootstrapped'"
            ).fetchone()[0]
            == 0
        )
    finally:
        catalog.close()


def test_catalog_migration_preserves_events_and_refuses_bootstrap_downgrade(
    tmp_path: Path,
) -> None:
    db = tmp_path / "prior-head.sqlite3"
    config = _alembic_config(db)
    command.upgrade(config, "0004")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO database_events "
        "(db_host, db_port, database_name, event_type, occurred_at, backup_id) "
        "VALUES ('localhost', 5432, 'tmp', 'dropped', datetime('now'), NULL)"
    )
    conn.commit()
    conn.close()

    command.upgrade(config, "head")
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM database_events").fetchone()[0] == 1
    assert conn.execute("SELECT sequence FROM database_events").fetchone()[0] == 1
    assert {row[1] for row in conn.execute("PRAGMA index_list(database_events)")} >= {
        "database_events_cluster_idx",
        "database_events_cluster_identity_idx",
    }
    assert ("backup_id", "backups", "id") in {
        (row[3], row[2], row[4]) for row in conn.execute("PRAGMA foreign_key_list(database_events)")
    }
    conn.execute(
        "INSERT INTO database_events "
        "(db_host, db_port, database_name, event_type, occurred_at, backup_id, "
        "source_kind, source_sha256, cluster_id, data_directory) "
        "VALUES ('localhost', 5432, 'tmp', 'bootstrapped', datetime('now'), NULL, "
        "NULL, NULL, 'cluster', '/safe/data')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="bootstrap provenance"):
        command.downgrade(config, "0004")


def test_bootstrap_executor_distinguishes_created_and_already_ready() -> None:
    context, executor, steps = _bootstrap_context()
    assert run_bootstrap_tmp(context, *steps) is BootstrapOutcome.CREATED
    assert [step.step_id for step in executor.executed] == ["probe", "spawn", "ready"]
    context.complete()

    context, executor, steps = _bootstrap_context(probe_stdout="installed")
    assert run_bootstrap_tmp(context, *steps) is BootstrapOutcome.ALREADY_READY
    assert [step.step_id for step in executor.executed] == ["probe"]
    context.complete()


def test_bootstrap_executor_failure_keeps_record_action_unconsumed() -> None:
    context, _executor, steps = _bootstrap_context(ready_stdout="uninstalled", include_actions=True)
    with pytest.raises(BootstrapFailedError, match="base module"):
        run_bootstrap_tmp(context, *steps[:3])
    assert not context.consumed(bootstrap_record_action().step_id)


def test_bootstrap_publication_failure_keeps_record_incomplete_and_unwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=catalog_path)
    _active_claim(catalog)
    catalog.close()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda **_kwargs: catalog_path,
    )
    data_directory = tmp_path / "bootstrap-data"
    data_directory.mkdir()
    cluster = MagicMock()
    cluster.owned = True
    cluster._project_id = "project"
    cluster.endpoint_host = "localhost"
    cluster.endpoint_port = 5432
    instance = MagicMock()
    instance._postgres_cluster = cluster
    instance.config.default_cwd = tmp_path
    instance.config.start_config = StartConfig(data_dir=str(data_directory))

    original_init = BackupCatalog.__init__

    def deny_bootstrap_insert(self: BackupCatalog, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)

        def authorize(
            action: int,
            table: str | None,
            _column: str | None,
            _database: str | None,
            _source: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_INSERT and table == "database_events":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self._conn.set_authorizer(authorize)

    monkeypatch.setattr(BackupCatalog, "__init__", deny_bootstrap_insert)
    context, _executor, process_steps = _bootstrap_context(include_actions=True)
    verify = PreparedAction(step_id="verify", read_only=True)

    with pytest.raises(BootstrapFailedError, match="bootstrap lifecycle publication failed"):
        ensure_project_bootstrap_tmp(instance, context, steps=(*process_steps, verify))

    record_step = bootstrap_record_action().step_id
    assert context.consumed(record_step)
    assert record_step in context._started_actions
    with sqlite3.connect(catalog_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM database_events WHERE event_type='bootstrapped'"
            ).fetchone()[0]
            == 0
        )


def test_bootstrap_publication_rejects_unowned_configured_data_directory(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-bootstrap-data"
    outside.mkdir()
    instance = MagicMock()
    instance.config.default_cwd = tmp_path
    instance.config.start_config = StartConfig(data_dir=str(outside))
    instance._postgres_cluster.owned = True

    with pytest.raises(BootstrapFailedError, match="not project-owned"):
        _record_bootstrap_event(instance)


def test_bootstrap_publication_rejects_missing_data_dir_without_following_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=catalog_path)
    _active_claim(catalog)
    catalog.close()
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda **_kwargs: catalog_path,
    )
    external = tmp_path / "external-filestore"
    external.mkdir()
    data_directory = tmp_path / ".odcli" / "filestore"
    data_directory.parent.mkdir()
    data_directory.symlink_to(external, target_is_directory=True)
    cluster = MagicMock()
    cluster.owned = True
    cluster._project_id = "project"
    cluster.endpoint_host = "localhost"
    cluster.endpoint_port = 5432
    instance = MagicMock()
    instance._postgres_cluster = cluster
    instance.config.default_cwd = tmp_path
    instance.config.start_config = StartConfig(data_dir=None)

    with pytest.raises(BootstrapFailedError, match="data directory is not configured"):
        _record_bootstrap_event(instance)

    with sqlite3.connect(catalog_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM database_events WHERE event_type='bootstrapped'"
            ).fetchone()[0]
            == 0
        )


def test_bootstrap_record_action_is_conditional_and_consumed_only_on_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = MagicMock()
    published: list[object] = []

    def publish(value: object) -> None:
        published.append(value)

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.dbprep.bootstrap._record_bootstrap_event",
        publish,
    )
    context, _executor, process_steps = _bootstrap_context(include_actions=True)
    verify = PreparedAction(step_id="verify", read_only=True)
    ensure_project_bootstrap_tmp(instance, context, steps=(*process_steps, verify))
    context.complete()
    assert published == [instance]

    context, _executor, process_steps = _bootstrap_context(
        probe_stdout="installed", include_actions=True
    )
    ensure_project_bootstrap_tmp(instance, context, steps=(*process_steps, verify))
    context.complete()
    assert published == [instance]


def test_bootstrap_plan_is_secret_free_and_public_surface_stays_unchanged(tmp_path: Path) -> None:
    steps = bootstrap_tmp_steps(
        command_prefix=("python3", "/opt/odoo/odoo-bin"),
        start_config=StartConfig(config_path=str(tmp_path / "odoo.conf")),
        db_host="127.0.0.1",
        db_port=5432,
        db_user="odoo",
        db_password="bootstrap-password-secret",
    )
    public = repr(tuple(step.public_projection() for step in steps))
    assert "bootstrap-password-secret" not in public
    action = bootstrap_record_action().public_projection()
    assert action.step_id == "catalog.bootstrap.tmp.record"
    assert action.mutating is True
    assert not hasattr(DatabaseResource, "record_database_bootstrapped")
    assert not hasattr(PostgresCluster, "record_database_bootstrapped")


def test_public_db_rm_cli_contract_keeps_required_safety_flags() -> None:
    result = CliRunner().invoke(cli, ["db", "rm", "--help"])
    assert result.exit_code == 0, result.output
    assert "--force-default" in result.output
    assert "--force-connections" in result.output
    assert "--yes" in result.output
