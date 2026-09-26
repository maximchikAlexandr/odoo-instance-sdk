from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.backup_maintenance import attach_auto_prune
from odoo_instance_sdk.internal.proc import PreparedAction, RecordingExecutor
from odoo_instance_sdk.models import (
    Backup,
    BackupFormat,
    BackupPrunePlan,
    BackupPruneResult,
    BackupRetentionPolicy,
    DevelopmentEnvironment,
    EnvironmentDatabaseMode,
    EnvironmentState,
)


def _backup() -> Backup:
    return Backup(
        id=uuid.uuid4(),
        source_base_url="https://odoo.example",
        database_name="demo",
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path="/backups/demo.zip",
        filename="demo.zip",
        size_bytes=1,
        sha256="sha",
        downloaded_at=datetime.now(UTC),
    )


def _environment(backup_id: uuid.UUID | None) -> DevelopmentEnvironment:
    return DevelopmentEnvironment(
        id=uuid.uuid4(),
        name="demo",
        repository_root="/repo",
        git_common_dir="/repo/.git",
        branch="main",
        base_ref="main",
        worktree_path="/worktree",
        generated_config_path="/config/odoo.conf",
        python_environment_path="/venv",
        python_environment_owned=True,
        dependency_lock_path="/repo/uv.lock",
        http_interface="127.0.0.1",
        http_port=8069,
        db_mode=EnvironmentDatabaseMode.COPY,
        backup_id=backup_id,
        state=EnvironmentState.READY,
        created_at=datetime.now(UTC),
    )


def _command(callback, executor: RecordingExecutor) -> Command[Backup]:
    action = PreparedAction(step_id="primary", action="primary", description="primary")

    def run(context):
        context.action(action.step_id)
        result = callback(context)
        context.complete_action(action.step_id)
        return result

    return Command.create(
        ExecutionPlan(steps=(action.public_projection(),)),
        run,
        (action,),
        executor=executor,
    )


class _Backups:
    def __init__(self, result: BackupPruneResult | None = None, error: Exception | None = None):
        self.plan = BackupPrunePlan(
            project_id="project-demo",
            policy=BackupRetentionPolicy(),
            policy_fingerprint="fingerprint",
            cutoff=datetime.now(UTC),
        )
        self.result = result or BackupPruneResult(plan=self.plan)
        self.error = error
        self.calls: list[tuple[uuid.UUID, ...]] = []

    def _build_prune_plan(self, project, *, policy):
        return self.plan

    def _execute_prune_plan(self, plan, *, dry_run, excluded_ids=()):
        self.calls.append(tuple(excluded_ids))
        if self.error is not None:
            raise self.error
        return self.result


def test_auto_prune_is_captured_after_primary_and_excludes_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_retention.read_retention_policy",
        lambda: BackupRetentionPolicy(auto_prune=True),
    )
    executor = RecordingExecutor()
    backups = _Backups()
    output = _backup()
    command = attach_auto_prune(
        _command(lambda _context: output, executor),
        backups=backups,
        project=Path("/project"),
    )

    assert [step.step_id for step in command.plan.steps] == ["primary", "backup.auto-prune"]
    assert command.run() is output
    assert backups.calls == [(output.id,)]


def test_auto_prune_failure_is_a_warning_after_primary_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_retention.read_retention_policy",
        lambda: BackupRetentionPolicy(auto_prune=True),
    )
    backups = _Backups(error=RuntimeError("maintenance unavailable"))
    command = attach_auto_prune(
        _command(lambda _context: _backup(), RecordingExecutor()),
        backups=backups,
        project=Path("/project"),
    )

    result = command.run()

    assert result.id == result.id
    assert result.warnings == ("automatic backup pruning failed; primary operation succeeded",)


def test_auto_prune_checkout_excludes_backup_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_retention.read_retention_policy",
        lambda: BackupRetentionPolicy(auto_prune=True),
    )
    backups = _Backups()
    output = _environment(uuid.uuid4())
    command = attach_auto_prune(
        _command(lambda _context: output, RecordingExecutor()),
        backups=backups,
        project=Path("/project"),
    )

    result = command.run()

    assert result is output
    assert backups.calls == [(output.backup_id,)]


@pytest.mark.parametrize("partial", [False, True])
def test_auto_prune_checkout_propagates_maintenance_warning(
    monkeypatch: pytest.MonkeyPatch,
    partial: bool,
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_retention.read_retention_policy",
        lambda: BackupRetentionPolicy(auto_prune=True),
    )
    backups = _Backups(
        error=RuntimeError("maintenance unavailable") if not partial else None,
    )
    if partial:
        backups.result = BackupPruneResult(plan=backups.plan, warnings=("partial",))
    output = _environment(uuid.uuid4())
    command = attach_auto_prune(
        _command(lambda _context: output, RecordingExecutor()),
        backups=backups,
        project=Path("/project"),
    )

    result = command.run()

    expected = (
        "automatic backup pruning completed with warnings"
        if partial
        else "automatic backup pruning failed; primary operation succeeded"
    )
    assert result.warnings == (expected,)
    assert result.backup_id == output.backup_id


def test_auto_prune_does_not_run_when_primary_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_retention.read_retention_policy",
        lambda: BackupRetentionPolicy(auto_prune=True),
    )
    backups = _Backups()

    def fail(_context):
        raise RuntimeError("primary failed")

    command = attach_auto_prune(
        _command(fail, RecordingExecutor()),
        backups=backups,
        project=Path("/project"),
    )

    with pytest.raises(RuntimeError, match="primary failed"):
        command.run()
    assert backups.calls == []


def test_auto_prune_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def policy() -> BackupRetentionPolicy:
        return BackupRetentionPolicy()

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.backup_retention.read_retention_policy",
        policy,
    )
    command = _command(lambda _context: _backup(), RecordingExecutor())

    assert (
        attach_auto_prune(
            command,
            backups=_Backups(),
            project=Path("/project"),
        )
        is command
    )
