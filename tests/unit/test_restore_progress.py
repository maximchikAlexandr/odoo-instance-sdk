from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.execution import ExecutionPlan
from odoo_instance_sdk.internal.proc import StepEvent, StepObserver
from odoo_instance_sdk.internal.restore_stages import (
    RESTORE_STAGE_IDS,
    restore_stage,
    restore_stage_heartbeat,
)
from odoo_instance_sdk.models import (
    DatabasePreparationAction,
    DatabasePreparationResult,
)


def test_restore_stage_ids_match_spec_contract() -> None:
    assert RESTORE_STAGE_IDS == (
        "backup_prepare",
        "auxiliary_start",
        "db_restore",
        "db_verify",
        "filestore_restore",
        "admin_reset",
        "default_switch",
    )


def test_restore_stage_publishes_started_completed_without_observer() -> None:
    """Without an active observer, stage publication is inert (machine modes)."""
    events: list[StepEvent] = []

    def capture(event: StepEvent) -> None:
        events.append(event)

    from odoo_instance_sdk.internal.proc import (
        RunContext,
        SubprocessExecutor,
    )

    context: RunContext[object] = RunContext((), SubprocessExecutor())
    assert context.observer is None

    with restore_stage("db_restore"):
        pass
    assert events == []


def test_restore_stage_emits_started_and_completed_through_observer() -> None:
    events: list[StepEvent] = []

    def capture(event: StepEvent) -> None:
        events.append(event)

    observer: StepObserver = capture
    with restore_stage("db_verify", observer=observer):
        pass
    assert [e.step_id for e in events] == ["db_verify", "db_verify"]
    assert [e.kind for e in events] == ["started", "completed"]


def test_restore_stage_heartbeat_emits_progress_events_without_wall_clock_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[StepEvent] = []

    def capture(event: StepEvent) -> None:
        events.append(event)

    observer: StepObserver = capture

    class ImmediateEvent:
        def __init__(self) -> None:
            self.calls = 0

        def wait(self, _timeout: float | None = None) -> bool:
            self.calls += 1
            return self.calls > 1

        def set(self) -> None:
            return None

        def is_set(self) -> bool:
            return False

    class InlineThread:
        def __init__(self, *, target: object, **_kwargs: object) -> None:
            self._target = target

        def start(self) -> None:
            self._target()  # type: ignore[operator]

    monkeypatch.setattr(threading, "Thread", InlineThread)
    monkeypatch.setattr(threading, "Event", ImmediateEvent)
    with restore_stage_heartbeat("db_restore", observer=observer, interval=0.05):
        pass

    kinds = [e.kind for e in events]
    assert kinds[0] == "started"
    assert "progress" in kinds
    assert kinds[-1] == "completed"
    assert all(e.step_id == "db_restore" for e in events)
    assert any(e.elapsed is not None for e in events if e.kind == "progress")


def test_restore_stage_heartbeat_is_inert_without_observer() -> None:
    """No observer means no heartbeat thread, no events, no stdout pollution."""
    events: list[StepEvent] = []

    with restore_stage_heartbeat("db_restore"):
        pass
    assert events == []


def test_restore_stage_failure_preserves_stage_id_and_elapsed() -> None:
    events: list[StepEvent] = []

    def capture(event: StepEvent) -> None:
        events.append(event)

    observer: StepObserver = capture
    with pytest.raises(RuntimeError, match="boom"), restore_stage("admin_reset", observer=observer):
        raise RuntimeError("boom")
    assert [e.kind for e in events] == ["started", "failed"]
    failure = events[-1]
    assert failure.step_id == "admin_reset"
    assert failure.error is not None
    assert failure.elapsed is not None


def test_long_restore_rich_indicator_updates_until_completion_without_stdout_pollution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A long restore without child stdout updates the Rich indicator until done.

    JSON/TOON keep a single document; terminal progress does not pollute
    machine stdout.
    """

    class ObservedCommand:
        plan = ExecutionPlan()

        def __init__(self) -> None:
            self.observer: StepObserver | None = None
            self.observe_output = False

        def run(
            self,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> DatabasePreparationResult:
            self.observer = observer
            self.observe_output = observe_output
            assert observer is not None
            for stage_id in RESTORE_STAGE_IDS:
                observer(StepEvent(step_id=stage_id, kind="started", elapsed=0.0))
                observer(StepEvent(step_id=stage_id, kind="completed", elapsed=0.1))
            return DatabasePreparationResult(
                mode=DatabasePreparationAction.RESTORE,
                restored_database="demo_copy",
                retained_artifacts=(),
            )

    client = MagicMock()
    command = ObservedCommand()
    client.environments.refresh_database_command.return_value = command
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(cli, ["db", "refresh", "--restore"])

    assert result.exit_code == 0, result.output
    assert command.observer is not None
    for stage_id in RESTORE_STAGE_IDS:
        assert f"[{stage_id}] started" in result.output
        assert f"[{stage_id}] completed" in result.output
    assert "demo_copy" in result.output


@pytest.mark.parametrize("mode_args", [["--format", "json"], ["--format", "toon"]])
def test_long_restore_machine_output_is_single_document_without_heartbeat_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode_args: list[str],
) -> None:
    """JSON/TOON keep one document; no heartbeat events pollute machine stdout."""

    class ObservedCommand:
        plan = ExecutionPlan()

        def run(
            self,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> DatabasePreparationResult:
            assert observer is None, "machine modes must not receive an observer"
            return DatabasePreparationResult(
                mode=DatabasePreparationAction.RESTORE,
                restored_database="demo_copy",
                retained_artifacts=(),
            )

    client = MagicMock()
    command = ObservedCommand()
    client.environments.refresh_database_command.return_value = command
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(cli, ["db", "refresh", "--restore", *mode_args])

    assert result.exit_code == 0, result.output
    if mode_args == ["--format", "json"]:
        document = json.loads(result.stdout)
        assert document["ok"] is True
        assert document["result"]["restored_database"] == "demo_copy"
        assert result.stdout.count('"ok"') == 1
    else:
        from toon import DecodeOptions, decode

        document = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert document["ok"] is True
        assert result.stdout.count("ok:") == 1
    for stage_id in RESTORE_STAGE_IDS:
        assert f"[{stage_id}]" not in result.stdout


def test_restore_failure_preserves_last_stage_id_and_elapsed_in_rich(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.internal.dbprep.source import (
        DatabasePreparationFailureContext,
    )

    failure = RuntimeError("db restore failed")
    failure.failure_context = DatabasePreparationFailureContext(  # type: ignore[attr-defined]
        restore_stage_id="db_restore",
        restore_stage_elapsed=12.5,
    )

    class FailingCommand:
        plan = ExecutionPlan()

        def run(
            self,
            *,
            observer: StepObserver | None = None,
            observe_output: bool = False,
        ) -> DatabasePreparationResult:
            raise failure

    client = MagicMock()
    client.environments.refresh_database_command.return_value = FailingCommand()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(cli, ["db", "refresh", "--restore"])

    assert result.exit_code == 1
    combined = result.stdout + result.stderr
    assert "db restore failed" in combined
    assert "restore stage db_restore" in combined
    assert "12.5s" in combined
