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
