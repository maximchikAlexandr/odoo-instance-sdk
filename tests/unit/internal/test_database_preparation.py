from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from odoo_instance_sdk.exceptions import (
    ConfigError,
    InstanceConfigurationError,
    LockConflictError,
    MasterPasswordRequiredError,
    OmittedStepError,
    UnplannedStepError,
)
from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.internal.proc import RecordingExecutor
from odoo_instance_sdk.models import (
    Backup,
    BackupBranchOrigin,
    BackupFormat,
    BackupFreshness,
    BackupProvenanceStatus,
    DatabasePreparationResult,
    DatabaseRefreshOptions,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.project import TestInstanceProjectConfig as ConfigTestInstance


@pytest.fixture(autouse=True)
def _approved_test_instance_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", "https://example.test:443")


def _project(tmp_path: Path, *, branch: str | None = "develop") -> ProjectConfig:
    return ProjectConfig(
        repository_root=tmp_path,
        test_instance=ConfigTestInstance(
            base_url="https://example.test",
            database="remote_test",
            git_branch=branch,
        ),
        default_source_database="source",
    )


def _backup(tmp_path: Path, *, downloaded_at: datetime) -> Backup:
    path = tmp_path / "backup.zip"
    path.write_bytes(b"backup")
    return Backup(
        id=uuid.uuid4(),
        source_base_url="https://example.test",
        database_name="remote_test",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(path),
        filename=path.name,
        size_bytes=6,
        sha256="a" * 64,
        downloaded_at=downloaded_at,
        source_git_branch="develop",
    )


def test_preparation_command_captures_restore_process_manifest_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore planning exposes Git, compose, and psql children in one snapshot."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: "/usr/bin/psql"
    )
    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_host = 127.0.0.1\n"
        "db_port = 5432\n"
        "db_user = odoo\n"
        "db_password = private\n"
    )
    project = ProjectConfig(
        repository_root=tmp_path,
        source_config=source,
        postgres=PostgresProjectConfig(
            mode="compose", image="postgres:16", port=55432, user="odoo"
        ),
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )

    command = DatabasePreparationCoordinator(MagicMock()).prepare_command(
        project,
        options=DatabaseRefreshOptions(restore=True),
    )

    process_ids = tuple(step.step_id for step in command.plan.process_steps)
    assert process_ids[:2] == (
        "database.prepare.git.toplevel",
        "database.prepare.git.common-dir",
    )
    assert process_ids[2:10] == (
        "postgres.ensure.image.pull",
        "postgres.ensure.image.inspect",
        "postgres.ensure.status.ps",
        "postgres.ensure.status.health",
        "postgres.ensure.config",
        "postgres.ensure.up",
        "postgres.ensure.final.ps",
        "postgres.ensure.final.health",
    )
    assert process_ids[-3:] == (
        "database.restore.exists-reservation",
        "database.restore.exists-before",
        "database.restore.exists-after",
    )


def test_preparation_command_runs_captured_git_steps_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real preparation command consumes the exact inspected process inputs."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.proc import PreparedProcess, ProcessResult, RecordingExecutor

    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    client = MagicMock()
    client.instance.return_value = remote

    def result_for(prepared: PreparedProcess) -> ProcessResult:
        argv = prepared.argv
        return ProcessResult(
            argv=argv,
            returncode=0,
            stdout=str(tmp_path),
            stderr="",
            duration=0.0,
            cwd=None,
            environment=(),
        )

    executor = RecordingExecutor(result_factory=result_for)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    command = DatabasePreparationCoordinator(client).prepare_command(
        _project(tmp_path), executor=executor
    )

    result = command.run()

    assert result.backup == backup
    planned = command.plan.process_steps
    assert tuple(step.step_id for step in executor.executed) == tuple(
        step.step_id for step in planned
    )
    assert len(executor.executed) == len({step.step_id for step in executor.executed})


def _production_restore_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    odoo_returncode: int = 0,
) -> tuple[
    Command[DatabasePreparationResult],
    RecordingExecutor,
    ProjectConfig,
    Backup,
    MagicMock,
]:
    """Build the public preparation command with real active adapters."""
    from odoo_instance_sdk.internal import database_preparation as preparation
    from odoo_instance_sdk.internal.database_preparation import (
        ProjectRuntimeBinding,
        RestorePreflight,
        resolve_test_source,
    )
    from odoo_instance_sdk.internal.proc import (
        PreparedProcess,
        PreparedStep,
        ProcessResult,
        RecordingExecutor,
        active_context,
    )
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: "/usr/bin/psql"
    )
    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
        "db_host = 127.0.0.1\n"
        "db_port = 5432\n"
        "db_user = odoo\n"
        "db_password = db-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        postgres=PostgresProjectConfig(
            mode="compose", image="postgres:16", port=55432, user="odoo"
        ),
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    client = MagicMock()
    client.instance.return_value = remote
    local = MagicMock()
    local.databases.restore.side_effect = _consume_restore_probes
    cluster = PostgresCluster._from_config(
        project,
        repository_root=tmp_path,
        compose_runner=None,
        project_id="<runtime>",
    )
    options = DatabaseRefreshOptions(restore=True, reset_admin_password=True)
    source_resolution = resolve_test_source(project, options)
    runtime = ProjectRuntimeBinding(str(python), str(odoo), tmp_path)

    @contextlib.contextmanager
    def fake_preflight(
        _client: object,
        _project: object,
        *,
        options: DatabaseRefreshOptions,
        wait_for_lock: bool = True,
        coalesce: bool = False,
        target_database: str | None = None,
    ) -> Iterator[RestorePreflight]:
        del _client, _project, wait_for_lock, coalesce
        context = active_context()
        assert context is not None
        context.action("database.prepare.lock")
        context.process("database.prepare.git.toplevel")
        context.process("database.prepare.git.common-dir")
        for step in cluster._ensure_running_steps(60.0):
            if isinstance(step, PreparedStep):
                context.process(step.step_id)
        yield RestorePreflight(
            project=project,
            project_id="<runtime>",
            source=source_resolution,
            source_config=source,
            local_instance=local,
            runtime=runtime,
            postgres_cluster=cluster,
            target_database=target_database or "remote_refresh_test",
        )

    def result_for(prepared: PreparedProcess) -> ProcessResult:
        prepared = cast("PreparedStep", prepared)
        return ProcessResult(
            argv=prepared.argv,
            returncode=odoo_returncode if prepared.step_id == "instance.shell_script" else 0,
            stdout="",
            stderr="odoo failed" if prepared.step_id == "instance.shell_script" else "",
            duration=0.0,
            cwd=prepared.cwd,
            environment=prepared.environment,
        )

    write = MagicMock()
    executor = RecordingExecutor(result_factory=result_for)
    monkeypatch.setattr(preparation, "_restore_preflight", fake_preflight)
    monkeypatch.setattr(ProjectConfig, "load", MagicMock(return_value=project))
    monkeypatch.setattr("odoo_instance_sdk.internal.project_manifest.write_manifest", write)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    command = preparation.DatabasePreparationCoordinator(client).prepare_command(
        project, options=options, executor=executor
    )
    return command, executor, project, backup, write


def _consume_restore_probes(*_args: object, **_kwargs: object) -> MagicMock:
    from odoo_instance_sdk.internal.proc import active_context

    context = active_context()
    assert context is not None
    context.process("database.restore.exists-reservation")
    context.process("database.restore.exists-before")
    context.process("database.restore.exists-after")
    return MagicMock()


def test_production_restore_command_consumes_compose_psql_and_odoo_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command, executor, _project_config, backup, write = _production_restore_command(
        tmp_path, monkeypatch
    )

    result = command.run()

    assert result.backup == backup
    planned = command.plan.process_steps
    assert tuple(step.step_id for step in executor.executed) == tuple(
        step.step_id for step in planned
    )
    from odoo_instance_sdk.internal.proc.redaction import project_process_step

    recorded_shell = project_process_step(executor.executed[-1])
    planned_shell = planned[-1]
    assert recorded_shell.input_preview == planned_shell.input_preview
    assert recorded_shell.cwd == planned_shell.cwd
    assert recorded_shell.environment_policy == planned_shell.environment_policy
    assert recorded_shell.environment_overrides == planned_shell.environment_overrides
    assert recorded_shell.timeout == planned_shell.timeout
    assert recorded_shell.mutating == planned_shell.mutating
    assert executor.executed[-1].wrapper_nonce is not None
    assert executor.executed[-1].wrapper_nonce.encode() in (executor.executed[-1].stdin or b"")
    assert len(executor.executed) == len({step.step_id for step in executor.executed})
    assert write.called


def test_production_restore_command_rolls_back_after_odoo_child_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command, executor, project, backup, write = _production_restore_command(
        tmp_path, monkeypatch, odoo_returncode=1
    )

    with pytest.raises(Exception, match="Administrator password reset failed") as failure:
        command.run()

    assert getattr(failure.value, "failure_context").retained_backup_id == backup.id
    target_step = next(
        step
        for step in command.plan.process_steps
        if step.step_id == "database.restore.exists-before"
    )
    assert getattr(failure.value, "failure_context").retained_database in target_step.argv[-1]
    assert project.default_source_database == "old"
    write.assert_not_called()
    assert tuple(step.step_id for step in executor.executed) == tuple(
        step.step_id for step in command.plan.process_steps
    )


def test_preparation_command_rejects_omitted_captured_child() -> None:
    """A successful callback cannot blanket-skip a required prepared child."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor

    child = PreparedStep(step_id="database.prepare.restore", argv=("restore",))
    command = DatabasePreparationCoordinator(MagicMock())._action_command(
        "database.prepare",
        "Prepare a database",
        lambda: "done",
        executor=RecordingExecutor(),
        steps=(child,),
    )

    with pytest.raises(OmittedStepError, match=r"database\.prepare\.restore"):
        command.run()


def test_preparation_command_rejects_substituted_child_before_launch() -> None:
    """A callback cannot replace an inspected child with an unplanned argv."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor, active_context

    executor = RecordingExecutor()
    planned = PreparedStep(step_id="database.prepare.restore", argv=("restore",))

    def substitute() -> str:
        context = active_context()
        assert context is not None
        context.process_prepared(PreparedStep(step_id="substituted", argv=("other",)))
        return "never"

    command = DatabasePreparationCoordinator(MagicMock())._action_command(
        "database.prepare",
        "Prepare a database",
        substitute,
        executor=executor,
        steps=(planned,),
    )

    with pytest.raises(UnplannedStepError, match="substituted"):
        command.run()
    assert executor.executed == []


def test_preparation_command_failure_consumes_restore_and_rollback_steps() -> None:
    """A restore failure records compensation without replaying its child."""
    from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessResult,
        RecordingExecutor,
        active_context,
    )

    restore = PreparedStep(step_id="database.prepare.restore", argv=("restore",))
    rollback = PreparedAction(
        step_id="database.prepare.rollback",
        action="compensate-preparation-failure",
        read_only=True,
    )
    executor = RecordingExecutor(
        default_result=ProcessResult(
            argv=restore.argv,
            returncode=1,
            stdout="",
            stderr="restore failed",
            duration=0.0,
            cwd=None,
            environment=(),
        )
    )

    def fail_restore() -> str:
        context = active_context()
        assert context is not None
        result = context.process(restore.step_id)
        assert isinstance(result, ProcessResult)
        context.action(rollback.step_id)
        raise RuntimeError("restore failed")

    command = DatabasePreparationCoordinator(MagicMock())._action_command(
        "database.prepare",
        "Prepare a database",
        fail_restore,
        executor=executor,
        steps=(restore, rollback),
        optional_steps=(rollback.step_id,),
    )

    with pytest.raises(RuntimeError, match="restore failed"):
        command.run()
    assert [step.step_id for step in executor.executed] == [restore.step_id]


def test_lock_paths_are_project_and_target_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_locks_dir", lambda: tmp_path)
    from odoo_instance_sdk.internal.locks import (
        database_preparation_artifact_lock_path,
        database_preparation_lock_path,
    )

    project_lock = database_preparation_lock_path("repo_123")
    target_lock = database_preparation_artifact_lock_path("repo_123", "db_refresh")
    assert project_lock == tmp_path / "database-preparation-repo_123.lock"
    assert target_lock == tmp_path / "database-preparation-repo_123-db_refresh.lock"
    with pytest.raises(ConfigError):
        database_preparation_artifact_lock_path("repo_123", "bad/name")


def test_cli_adapters_do_not_own_preparation_locks() -> None:
    commands = Path(__file__).parents[3] / "src" / "odoo_instance_sdk" / "commands"
    for source in commands.glob("*.py"):
        text = source.read_text()
        assert "database_preparation_lock_path" not in text
        assert "database_preparation_artifact_lock_path" not in text


@pytest.mark.parametrize(
    ("explicit", "configured", "expected", "origin"),
    [
        (" release/19 ", "develop", "release/19", BackupBranchOrigin.EXPLICIT),
        (None, " develop ", "develop", BackupBranchOrigin.CONFIGURED),
        (None, None, None, BackupBranchOrigin.UNKNOWN),
    ],
)
def test_resolve_test_source_branch_precedence(
    tmp_path: Path,
    explicit: str | None,
    configured: str | None,
    expected: str | None,
    origin: BackupBranchOrigin,
) -> None:
    from odoo_instance_sdk.internal.database_preparation import resolve_test_source

    project = _project(tmp_path, branch=configured)
    source = resolve_test_source(project, DatabaseRefreshOptions(source_branch=explicit))
    assert source.branch == expected
    assert source.origin is origin


@pytest.mark.parametrize(
    ("expected", "recorded", "status"),
    [
        ("main", "main", BackupProvenanceStatus.MATCHED),
        ("refs/heads/main", "main", BackupProvenanceStatus.MATCHED),
        ("main", "develop", BackupProvenanceStatus.MISMATCHED),
        ("main", None, BackupProvenanceStatus.UNKNOWN),
    ],
)
def test_provenance_comparison_normalizes_only_heads_prefix(
    expected: str, recorded: str | None, status: BackupProvenanceStatus
) -> None:
    from odoo_instance_sdk.internal.database_preparation import compare_provenance

    comparison = compare_provenance(expected, recorded)
    assert comparison.status is status


def test_freshness_boundaries(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.database_preparation import classify_freshness
    from odoo_instance_sdk.models import NoBackup

    now = datetime(2026, 8, 27, 12, tzinfo=UTC)
    assert classify_freshness(None, 1, now=now) is BackupFreshness.MISSING
    assert classify_freshness(NoBackup(), 1, now=now) is BackupFreshness.MISSING
    missing = _backup(tmp_path, downloaded_at=now)
    Path(missing.path).unlink()
    assert classify_freshness(missing, 1, now=now) is BackupFreshness.UNAVAILABLE
    assert (
        classify_freshness(_backup(tmp_path, downloaded_at=now - timedelta(hours=1)), 1, now=now)
        is BackupFreshness.STALE
    )
    assert (
        classify_freshness(_backup(tmp_path, downloaded_at=now), 1, now=now)
        is BackupFreshness.FRESH
    )
    assert (
        classify_freshness(_backup(tmp_path, downloaded_at=now - timedelta(days=10)), None, now=now)
        is BackupFreshness.FRESH
    )


def test_target_name_is_valid_and_utf8_bounded() -> None:
    from odoo_instance_sdk.internal.database_preparation import generate_target_database
    from odoo_instance_sdk.internal.db_name import validate_db_name

    name = generate_target_database(
        "source_" + "x" * 100,
        now=datetime(2026, 9, 3, 9, 4, 20, tzinfo=UTC),
        suffix="2ee3a458a068",
    )
    validate_db_name(name)
    assert len(name.encode("utf-8")) <= 63
    assert name.endswith("_20260903090420_2ee3a458a068")
    assert "_refresh_" not in name


def test_target_name_omits_refresh_marker() -> None:
    from odoo_instance_sdk.internal.database_preparation import generate_target_database

    assert (
        generate_target_database(
            "KOM-307_4",
            now=datetime(2026, 9, 3, 9, 4, 20, tzinfo=UTC),
            suffix="2ee3a458a068",
        )
        == "KOM-307_4_20260903090420_2ee3a458a068"
    )


def test_target_reservation_rechecks_collisions() -> None:
    from odoo_instance_sdk.internal.database_preparation import reserve_target_database

    candidates = iter(("source_refresh_one", "source_refresh_two"))
    seen: list[str] = []

    def generate(_: str) -> str:
        return next(candidates)

    def exists(name: str) -> bool:
        seen.append(name)
        return name.endswith("one")

    assert reserve_target_database("remote", exists, generator=generate) == "source_refresh_two"
    assert seen == ["source_refresh_one", "source_refresh_two"]


def test_manifest_conflicts_ignore_repository_identity() -> None:
    from odoo_instance_sdk.internal.database_preparation import relevant_manifest_conflicts

    left = ProjectConfig(repository_root=Path("/one"), default_source_database="a")
    right = ProjectConfig(repository_root=Path("/two"), default_source_database="b")
    assert relevant_manifest_conflicts(left, right) == ("default_source_database",)


def test_download_preparation_reads_secret_before_lock_and_never_requires_local_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    project = _project(tmp_path)
    loaded = MagicMock(return_value=project)
    monkeypatch.setattr(ProjectConfig, "load", loaded)
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    client = MagicMock()
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    client.instance.return_value.databases.backup.return_value = backup

    result = preparation.prepare_download(client, project)
    preparation.prepare_download(client, project)

    assert result.backup == backup
    assert result.source_git_branch == "develop"
    assert result.branch_origin is BackupBranchOrigin.CONFIGURED
    assert client.instance.call_count == 2
    client.instance.assert_called_with("https://example.test", master_password="remote-secret")
    assert client.instance.return_value.databases.backup.call_count == 2
    client.instance.return_value.databases.backup.assert_called_with(
        "remote_test", source_git_branch="develop"
    )


def test_missing_remote_secret_fails_before_client_or_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    monkeypatch.delenv("ODCLI_TEST_MASTER_PASSWORD", raising=False)
    client = MagicMock()
    lock = MagicMock()
    monkeypatch.setattr(preparation, "exclusive_lock", lock)
    with pytest.raises(MasterPasswordRequiredError):
        preparation.prepare_download(client, _project(tmp_path))
    client.instance.assert_not_called()
    lock.assert_not_called()


def test_restore_missing_remote_secret_fails_before_preparation_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    monkeypatch.delenv("ODCLI_TEST_MASTER_PASSWORD", raising=False)
    client = MagicMock()
    lock = MagicMock()
    cluster = MagicMock()
    monkeypatch.setattr(preparation, "exclusive_lock", lock)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project", cluster
    )

    with pytest.raises(MasterPasswordRequiredError):
        preparation.prepare_restore(client, _project(tmp_path))

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()
    lock.assert_not_called()
    cluster.assert_not_called()


def test_unpinned_download_preparation_fails_before_lock_or_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    monkeypatch.delenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", raising=False)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    client = MagicMock()
    lock = MagicMock()
    monkeypatch.setattr(preparation, "exclusive_lock", lock)

    with pytest.raises(ConfigError, match="not approved outside the repository"):
        preparation.prepare_download(client, _project(tmp_path))

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()
    lock.assert_not_called()


def test_unpinned_restore_preflight_fails_before_lock_or_local_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    monkeypatch.delenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", raising=False)
    client = MagicMock()
    lock = MagicMock()
    cluster = MagicMock()
    monkeypatch.setattr(preparation, "exclusive_lock", lock)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project", cluster
    )

    with pytest.raises(ConfigError, match="not approved outside the repository"):
        preparation.preflight_restore(client, _project(tmp_path))

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()
    lock.assert_not_called()
    cluster.assert_not_called()


def test_project_runtime_executable_cannot_read_remote_master_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.server import run_command

    executable = tmp_path / "inspect-environment"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "print('present' if os.getenv('ODCLI_TEST_MASTER_PASSWORD') else 'absent')\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    result = run_command(str(executable), [])

    assert result.returncode == 0
    assert result.stdout.strip() == "absent"


def test_restore_preflight_orders_lock_cluster_manager_and_target_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    events: list[str] = []

    @contextlib.contextmanager
    def lock(_: Path) -> Iterator[None]:
        events.append("preparation-lock")
        yield

    cluster = MagicMock()

    def ensure_cluster(**_: object) -> None:
        events.append("cluster")

    cluster.ensure_running.side_effect = ensure_cluster
    local = MagicMock()

    def list_names() -> tuple[str, ...]:
        events.append("names")
        return ("source",)

    def database_exists(_: str) -> bool:
        events.append("exists")
        return False

    local.databases.names.side_effect = list_names
    local.databases.exists.side_effect = database_exists
    client = MagicMock()
    client.instance.from_config.return_value = local
    monkeypatch.setattr(ProjectConfig, "load", MagicMock(return_value=project))
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lock)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        MagicMock(return_value=cluster),
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    preflight = preparation.preflight_restore(client, project)

    assert preflight.target_database
    assert events == ["preparation-lock", "cluster", "names", "exists"]
    client.instance.assert_not_called()
    client.instance.from_config.assert_called_once()


@pytest.mark.parametrize("entrypoint", ["preflight_restore", "prepare_restore"])
def test_restore_entrypoints_reject_invalid_local_config_before_network_or_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 0.0.0.0\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
    )
    project = ProjectConfig(
        repository_root=tmp_path,
        source_config=source,
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())
    monkeypatch.setattr(
        preparation, "exclusive_lock_until", lambda *_args, **_kwargs: contextlib.nullcontext()
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    client = MagicMock()

    with pytest.raises(InstanceConfigurationError, match="local source config must bind"):
        getattr(preparation, entrypoint)(client, project)

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["preflight_restore", "prepare_restore"])
def test_restore_entrypoints_report_lock_contention_consistently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    @contextlib.contextmanager
    def fail_lock(*_args: object, **_kwargs: object) -> Iterator[None]:
        raise LockConflictError("database-preparation-repo.lock", mode="exclusive")
        yield

    monkeypatch.setattr(preparation, "exclusive_lock", fail_lock)
    monkeypatch.setattr(preparation, "exclusive_lock_until", fail_lock)
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    client = MagicMock()

    with pytest.raises(LockConflictError, match=r"database-preparation-repo\.lock"):
        getattr(preparation, entrypoint)(client, _project(tmp_path))

    client.instance.assert_not_called()
    client.get_catalog.assert_not_called()


def test_target_instance_is_target_only_secure_and_ephemeral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.database_preparation import (
        ProjectRuntimeBinding,
        build_target_instance,
    )

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source,other\n"
        "dbfilter = source|other\n"
        "admin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_locks_dir", lambda: tmp_path / "locks"
    )
    runtime = ProjectRuntimeBinding(str(python), str(odoo), tmp_path)
    cluster = MagicMock()
    client = MagicMock()

    with build_target_instance(
        client,
        source_config=source,
        target_database="source_refresh_1",
        runtime=runtime,
        postgres_cluster=cluster,
        project_id="repo",
    ) as target:
        start_config = target.config.start_config
        assert start_config is not None
        generated = Path(start_config.config_path or "")
        assert generated.is_file()
        assert os.stat(generated).st_mode & 0o777 == 0o600
        assert target.config.configured_database_names == ("source_refresh_1",)
        assert start_config.db_name == "source_refresh_1"
        assert start_config.dbfilter == "source_refresh_1"
        assert target.config.command_prefix == (str(python), str(odoo))
        assert target.config.default_cwd == tmp_path
        assert target._postgres_cluster is cluster
        assert target._artifact_lock_path == (
            tmp_path / "locks" / "database-preparation-repo-source_refresh_1.lock"
        )
    assert not generated.exists()


def test_restore_coordinator_switches_default_only_after_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation
    from odoo_instance_sdk.internal.project_manifest import write_manifest

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_name = source\n"
        "admin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    local = MagicMock()
    local.databases.names.return_value = ("source",)
    local.databases.exists.return_value = False
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    cluster = MagicMock()
    client = MagicMock()
    client.instance.from_config.return_value = local
    client.instance.return_value = remote
    loader = MagicMock(return_value=project)
    monkeypatch.setattr(ProjectConfig, "load", loader)
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(
        preparation, "exclusive_lock_until", lambda *_args, **_kwargs: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        MagicMock(return_value=cluster),
    )
    monkeypatch.setattr(preparation, "write_manifest", write_manifest, raising=False)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    result = preparation.prepare_restore(client, project)

    assert result.default_switched is True
    assert result.previous_default == "old"
    assert result.effective_default == result.restored_database
    remote.databases.backup.assert_called_once_with("remote", source_git_branch=None)
    local.databases.restore.assert_called_once_with(
        backup,
        result.restored_database,
        copy=True,
        neutralize_database=True,
    )
    assert loader.return_value.default_source_database == "old"
    assert loader.call_count >= 1


def test_restore_failure_retains_backup_and_does_not_write_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        "db_name = source\nadmin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    local = MagicMock()
    local.databases.names.return_value = ("source",)
    local.databases.exists.return_value = False
    local.databases.restore.side_effect = RuntimeError("restore failed")
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    cluster = MagicMock()
    client = MagicMock()
    client.instance.from_config.return_value = local
    client.instance.return_value = remote
    write = MagicMock()
    loader = MagicMock(return_value=project)
    monkeypatch.setattr(ProjectConfig, "load", loader)
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(
        preparation, "exclusive_lock_until", lambda *_args, **_kwargs: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        MagicMock(return_value=cluster),
    )
    monkeypatch.setattr(preparation, "write_manifest", write, raising=False)
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    with pytest.raises(RuntimeError, match="restore failed") as failure:
        preparation.prepare_restore(client, project)

    assert backup.path and Path(backup.path).is_file()
    assert "retained backup" in " ".join(failure.value.__notes__ or ())
    write.assert_not_called()


def test_restore_admin_reset_failure_retains_target_and_removes_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        "db_name = source\nadmin_passwd = local-secret\n"
    )
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    odoo = tmp_path / "odoo-bin"
    odoo.write_text("#!/bin/sh\n")
    odoo.chmod(0o755)
    project = ProjectConfig(
        repository_root=tmp_path,
        python=python,
        odoo_bin=odoo,
        source_config=source,
        default_source_database="old",
        test_instance=ConfigTestInstance(base_url="https://example.test", database="remote"),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    local = MagicMock()
    local.databases.names.return_value = ("source",)
    local.databases.exists.return_value = False
    local.databases.restore.return_value = object()
    remote = MagicMock()
    remote.databases.backup.return_value = backup
    cluster = MagicMock()
    client = MagicMock()
    client.instance.from_config.return_value = local
    client.instance.return_value = remote
    monkeypatch.setattr(ProjectConfig, "load", MagicMock(return_value=project))
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(
        preparation,
        "exclusive_lock_until",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.PostgresCluster.from_project",
        MagicMock(return_value=cluster),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.instance.OdooInstance._run_shell_script_exclusive",
        MagicMock(side_effect=RuntimeError("reset failed")),
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    with pytest.raises(Exception, match="Administrator password reset failed") as failure:
        preparation.prepare_restore(
            client,
            project,
            options=DatabaseRefreshOptions(restore=True, reset_admin_password=True),
        )

    assert "retained database" in " ".join(failure.value.__notes__ or ())
    assert not list(tmp_path.glob(".odcli-refresh-*.conf"))
    assert project.default_source_database == "old"
    local.databases.restore.assert_called_once()


def test_pinned_http_download_reaches_remote_database_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    project = ProjectConfig(
        repository_root=tmp_path,
        test_instance=ConfigTestInstance(
            base_url="http://example.test:8069",
            database="remote_test",
        ),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    client = MagicMock()
    client.instance.return_value.databases.backup.return_value = backup
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")
    monkeypatch.setenv("ODCLI_TEST_INSTANCE_ORIGIN_PINS", "http://example.test:8069")
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setattr(preparation, "exclusive_lock", lambda _: contextlib.nullcontext())

    result = preparation.prepare_download(client, project)

    assert result.backup == backup
    client.instance.assert_called_once_with(
        "http://example.test:8069", master_password="remote-secret"
    )
    client.instance.return_value.databases.backup.assert_called_once_with(
        "remote_test", source_git_branch=None
    )


def test_checkout_coalesces_fresh_result_under_preparation_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal import database_preparation as preparation

    source = tmp_path / "odoo.conf"
    source.write_text(
        "[options]\nhttp_interface = 127.0.0.1\nhttp_port = 8069\n"
        "db_host = localhost\ndb_name = old\nadmin_passwd = local-secret\n"
    )
    project = ProjectConfig(
        repository_root=tmp_path,
        source_config=source,
        default_source_database="old",
        refresh_after_hours=1,
        test_instance=ConfigTestInstance(
            base_url="https://example.test", database="remote", git_branch="develop"
        ),
    )
    backup = _backup(tmp_path, downloaded_at=datetime.now(UTC))
    backup = Backup(
        id=backup.id,
        source_base_url=backup.source_base_url,
        database_name=backup.database_name,
        format=backup.format,
        filestore_requested=backup.filestore_requested,
        path=backup.path,
        filename=backup.filename,
        size_bytes=backup.size_bytes,
        sha256=backup.sha256,
        downloaded_at=backup.downloaded_at,
        source_git_branch="develop",
    )
    catalog = MagicMock()
    catalog.latest_restore.return_value = backup
    client = MagicMock()
    client.get_catalog.return_value = catalog
    monkeypatch.setattr(
        preparation,
        "_wait_for_preparation_lock",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        preparation, "canonical_project_identity", lambda _: (tmp_path, tmp_path, "repo")
    )
    monkeypatch.setenv("ODCLI_TEST_MASTER_PASSWORD", "remote-secret")

    result = preparation.prepare_restore(
        client,
        project,
        options=DatabaseRefreshOptions(restore=True),
        coalesce=True,
    )

    assert result.default_switched is False
    assert result.effective_default == "old"
    client.instance.assert_not_called()
