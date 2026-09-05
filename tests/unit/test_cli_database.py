from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import ResolvedContext
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.database_preparation import DatabasePreparationFailureContext
from odoo_instance_sdk.internal.proc import StepEvent, StepObserver
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    DatabasePreparationAction,
    DatabasePreparationResult,
)


def _resolved_context(client: object, source: object, instance: object) -> ResolvedContext:
    return ResolvedContext(client=client, source=source, instance=instance, provenance="explicit")  # type: ignore[arg-type]


def _command(value: object = None, *, error: BaseException | None = None) -> Command[object]:
    def run(_context: object) -> object:
        if error is not None:
            raise error
        return value

    return Command.create(ExecutionPlan(), run)


def test_db_help_registers_both_commands_without_password_option() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["db", "--help"])
    refresh_help = runner.invoke(cli, ["db", "refresh", "--help"])
    reset_help = runner.invoke(cli, ["db", "reset-admin-password", "--help"])

    assert result.exit_code == 0
    assert "refresh" in result.output
    assert "reset-admin-password" in result.output
    assert refresh_help.exit_code == 0
    assert reset_help.exit_code == 0
    assert "--password" not in refresh_help.output
    assert "--show-command-output" in refresh_help.output
    assert "--password" not in reset_help.output
    assert "[y/n]" not in refresh_help.output.lower()
    assert "[y/n]" not in reset_help.output.lower()


def test_refresh_reset_option_is_click_usage_error_before_sdk_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(cli, ["db", "refresh", "--reset-admin-password"])

    assert result.exit_code == 2
    assert "requires --restore" in result.stderr
    client.environments.refresh_database.assert_not_called()


@pytest.mark.parametrize("format_args", [["--json"], ["--format", "json"], ["--format", "toon"]])
def test_refresh_show_command_output_is_rich_only_before_sdk_work(
    monkeypatch: pytest.MonkeyPatch, format_args: list[str]
) -> None:
    client = MagicMock()
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path",
        lambda _ctx: pytest.fail("machine stream validation must precede project resolution"),
    )

    result = CliRunner().invoke(
        cli, ["db", "refresh", "--restore", "--show-command-output", *format_args]
    )

    assert result.exit_code == 2
    assert "only available with Rich output" in result.stderr
    client.environments.refresh_database_command.assert_not_called()


def test_refresh_uses_project_context_options_and_typed_machine_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = MagicMock()
    client.environments.refresh_database_command.return_value = _command(
        DatabasePreparationResult(
            mode=DatabasePreparationAction.RESTORE,
            restored_database="demo_copy",
            retained_artifacts=("backup.zip",),
        )
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(
        cli,
        [
            "db",
            "refresh",
            "--restore",
            "--reset-admin-password",
            "--source-branch",
            "release/19",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["ok"] is True
    assert document["result"]["restored_database"] == "demo_copy"
    assert document["result"]["retained_artifacts"] == ["backup.zip"]
    client.environments.refresh_database_command.assert_called_once()
    options = client.environments.refresh_database_command.call_args.kwargs["options"]
    assert options.restore is True
    assert options.reset_admin_password is True
    assert options.source_branch == "release/19"


def test_rich_restore_wires_step_observer_without_changing_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
            observer(StepEvent(step_id="restore.copy", kind="started"))
            observer(StepEvent(step_id="restore.copy", kind="completed", returncode=0))
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
    assert command.observe_output is False
    assert "[restore.copy] started" in result.output
    assert "[restore.copy] completed (exit 0)" in result.output
    assert "demo_copy" in result.output


def test_reset_delegates_only_for_exact_recorded_local_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = MagicMock()
    instance.config.configured_database_names = ("demo_copy",)
    instance.databases.reset_admin_password_command.return_value = _command(
        AdminPasswordResetResult(database="demo_copy", completed=True, xml_id="base.user_admin")
    )
    environment = SimpleNamespace(id="env-1", source_db_name=None, target_db_name="demo_copy")
    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.ready_instance",
        lambda _ctx: _resolved_context(MagicMock(), environment, instance),
    )

    result = CliRunner().invoke(cli, ["db", "reset-admin-password", "--json"])

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["context"] == {"environment_id": "env-1"}
    assert document["result"]["database"] == "demo_copy"
    assert "password" not in document["result"]
    instance.databases.reset_admin_password_command.assert_called_once_with()

    instance.config.configured_database_names = ("other",)
    rejected = CliRunner().invoke(cli, ["db", "reset-admin-password", "--json"])
    assert rejected.exit_code == 1
    assert "demo_copy" not in rejected.stdout
    assert "other" not in rejected.stdout


@pytest.mark.parametrize("output_format", ["json", "toon", None])
def test_refresh_failure_renders_typed_retained_context_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output_format: str | None,
) -> None:
    client = MagicMock()
    failure = RuntimeError("reset failed: master_pwd=remote-password-sentinel")
    failure.failure_context = DatabasePreparationFailureContext(  # type: ignore[attr-defined]
        retained_backup_id=uuid.UUID("00000000-0000-0000-0000-000000000042"),
        retained_database="demo_refresh_42",
    )
    client.environments.refresh_database_command.return_value = _command(error=failure)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    args = ["db", "refresh"]
    if output_format is not None:
        args.extend(["--format", output_format])
    result = CliRunner().invoke(cli, args)

    assert result.exit_code == 1
    assert "remote-password-sentinel" not in (result.stdout + result.stderr)
    assert "00000000-0000-0000-0000-000000000042" in (result.stdout + result.stderr)
    assert "demo_refresh_42" in (result.stdout + result.stderr)
    if output_format == "json":
        document = json.loads(result.stdout)
        assert document["context"] == {
            "retained_backup_id": "00000000-0000-0000-0000-000000000042",
            "retained_database": "demo_refresh_42",
        }
    elif output_format == "toon":
        from toon import DecodeOptions, decode

        document = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert document["context"]["retained_backup_id"] == ("00000000-0000-0000-0000-000000000042")
        assert document["context"]["retained_database"] == "demo_refresh_42"
