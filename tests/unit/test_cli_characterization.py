from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.commands.context import CliContext
from odoo_instance_sdk.commands.output import OutputMode, build_envelope
from odoo_instance_sdk.internal.context import resolve_environment, resolve_project
from odoo_instance_sdk.internal.database_preparation import DatabasePreparationCoordinator
from odoo_instance_sdk.models import Snapshot
from odoo_instance_sdk.resources.backup import BackupResource
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.environment import EnvironmentResource
from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES

ROOT_HELP_SNAPSHOT = """Usage: cli [OPTIONS] COMMAND [ARGS]...

Options:
  --version       Show the version and exit.
  --project PATH  Explicit project path.
  --env TEXT      Environment selector (UUID or name).
  --help          Show this message and exit.

Commands:
  db            Prepare and reset project databases.
  deps
  doctor
  env
  eval
  exec
  init
  logs
  module
  monitor       Start the observability monitor (FastAPI + React UI).
  postgres      Project-level PostgreSQL cluster lifecycle (read-only /...
  run
  shell
  test
  translations
  vscode
"""

MODULE_HELP_SNAPSHOT = """Usage: cli module [OPTIONS] COMMAND [ARGS]...

Options:
  --help  Show this message and exit.

Commands:
  list
  test
  update
"""

MODULE_TEST_HELP_SNAPSHOT = """Usage: cli module test [OPTIONS] MODULES...

Options:
  --test-tags TEXT           Test tags.  [required]
  --reload-tests
  --allow-empty
  --dry-run                  Plan only.
  --format [rich|json|toon]  Output format (default: rich).
  --json                     Emit JSON envelope.
  --help                     Show this message and exit.
"""


def _command(path: tuple[str, ...]) -> click.Command:
    command: click.Command = cli
    for name in path:
        assert isinstance(command, click.Group)
        command = command.commands[name]
    return command


def _option_names(command: click.Command) -> set[str]:
    return {option for param in command.params for option in param.opts}


def _passthrough_instance(
    instance: object,
    args: list[str],
    *,
    input_text: str = "",
) -> Result:
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=(MagicMock(), SimpleNamespace(), instance),
    ):
        return CliRunner().invoke(cli, args, input=input_text)


def test_cli_import_and_console_script_surface_are_stable() -> None:
    module = importlib.import_module("odoo_instance_sdk.cli")
    assert module.cli is cli
    assert cli.name == "cli"

    entrypoints = importlib.metadata.entry_points(group="console_scripts")
    odcli = next(entry for entry in entrypoints if entry.name == "odcli")
    assert odcli.value == "odoo_instance_sdk.cli:cli"
    assert odcli.load() is cli


def test_typed_cli_seam_and_reusable_resolvers_have_no_click_context_parameter() -> None:
    assert CliContext.__slots__ == (
        "project",
        "env",
        "project_source",
        "environment_source",
        "resolved_project",
        "resolved_environment",
    )
    context = CliContext()
    assert context.resolved_project is None
    assert context.resolved_environment is None
    assert set(OutputMode) == {OutputMode.RICH, OutputMode.JSON, OutputMode.TOON}
    assert "click.Context" not in str(inspect.signature(resolve_project))
    assert "click.Context" not in str(inspect.signature(resolve_environment))
    envelope = build_envelope(
        ok=True,
        command="characterization",
        result={"value": 1},
    )
    assert envelope["result"] == envelope["data"] == {"value": 1}
    assert json.dumps(envelope, indent=2) == (
        '{\n  "schema_version": 1,\n  "ok": true,\n  "command": "characterization",\n'
        '  "context": {},\n  "provenance": {},\n  "dry_run": false,\n'
        '  "warnings": [],\n  "result": {\n    "value": 1\n  },\n'
        '  "data": {\n    "value": 1\n  }\n}'
    )


def test_cli_tree_help_and_root_selectors_are_stable() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert {param.name for param in cli.params} == {"project", "env_selector", "version"}
    assert set(cli.list_commands(click.Context(cli))) == {
        "init",
        "env",
        "db",
        "run",
        "logs",
        "shell",
        "doctor",
        "eval",
        "exec",
        "test",
        "module",
        "translations",
        "deps",
        "vscode",
        "postgres",
        "monitor",
    }
    assert "--project" in result.output
    assert "--env" in result.output


def test_prechange_root_and_module_help_snapshots_are_stable() -> None:
    runner = CliRunner()
    assert runner.invoke(cli, ["--help"]).output == ROOT_HELP_SNAPSHOT
    assert runner.invoke(cli, ["module", "--help"]).output == MODULE_HELP_SNAPSHOT
    assert runner.invoke(cli, ["module", "test", "--help"]).output == MODULE_TEST_HELP_SNAPSHOT


def test_command_local_json_placement_is_stable() -> None:
    for case in PUBLIC_LEAF_CASES:
        if not case.is_bounded:
            continue
        path = case.path
        command = _command(path)
        assert "--json" in _option_names(command), path
        help_result = CliRunner().invoke(cli, [*path, "--help"])
        assert help_result.exit_code == 0
        assert "--json" in help_result.output

    for path in (("logs",), ("monitor",)):
        assert "--json" not in _option_names(_command(path)), path
    for path in (("run",), ("shell",)):
        assert "--dry-run" in _option_names(_command(path)), path
        assert "--json" in _option_names(_command(path)), path

    root_json = CliRunner().invoke(cli, ["--json", "env", "list"])
    assert root_json.exit_code == 2
    assert root_json.stdout == ""
    assert "No such option" in root_json.stderr


@pytest.mark.parametrize("leaf", ["run", "shell"])
@pytest.mark.parametrize(
    "option",
    [
        ("--json",),
        ("--format", "rich"),
        ("--format", "json"),
        ("--format", "toon"),
    ],
)
def test_raw_stream_output_options_require_dry_run_before_sdk_resolution(
    leaf: str, option: tuple[str, ...]
) -> None:
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        side_effect=AssertionError("raw option validation must precede SDK resolution"),
    ):
        result = CliRunner().invoke(cli, [leaf, *option])
    assert result.exit_code == 2
    assert "require --dry-run" in result.stderr


@pytest.mark.parametrize("leaf", ["run", "shell"])
@pytest.mark.parametrize(
    "option",
    [
        (),
        ("--format", "rich"),
        ("--format", "json"),
        ("--format", "toon"),
        ("--json",),
    ],
)
def test_raw_stream_dry_run_emits_one_captured_command_without_running(
    leaf: str, option: tuple[str, ...]
) -> None:
    from odoo_instance_sdk.execution import Command, ExecutionPlan, ProcessStep
    from odoo_instance_sdk.internal.proc import PreparedStep, RecordingExecutor

    executor = RecordingExecutor()
    effects: list[str] = []
    prepared = PreparedStep(
        step_id=f"instance.{leaf}",
        argv=("odoo", "--stop-after-init"),
        mutating=False,
    )

    def callback(_context: object) -> int:
        effects.append("run")
        return 0

    command = Command.create(
        ExecutionPlan(
            steps=(
                ProcessStep(
                    step_id=f"instance.{leaf}",
                    argv=prepared.argv,
                    display="odoo --stop-after-init",
                    executable="odoo",
                ),
            )
        ),
        callback,
        (prepared,),
        executor=executor,
    )
    instance = MagicMock()
    if leaf == "run":
        instance.run_foreground_command.return_value = command
    else:
        instance.shell_command.return_value = command
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance",
            return_value=(MagicMock(), SimpleNamespace(), instance),
        ),
        patch("odoo_instance_sdk.cli.cli_context._check_port_free", return_value=True),
    ):
        result = CliRunner().invoke(cli, [leaf, "--dry-run", *option])
    assert result.exit_code == 0, result.output
    if option in {(), ("--format", "rich")}:
        assert "Plan: " + leaf in result.stdout
        assert "instance." + leaf in result.stdout
    else:
        if option == ("--format", "json") or option == ("--json",):
            payload = json.loads(result.stdout)
        else:
            from toon import DecodeOptions, decode

            payload = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert payload["dry_run"] is True
        assert payload["result"]["steps"][0]["step_id"] == f"instance.{leaf}"
    assert effects == []
    assert executor.executed == []


def test_discovered_public_methods() -> None:
    expected = {
        EnvironmentResource: (
            "checkout",
            "checkout_command",
            "checkout_with_plan",
            "get",
            "list",
            "open_pgadmin",
            "open_pgadmin_command",
            "plan_checkout",
            "record_use",
            "refresh_database",
            "refresh_database_command",
            "remove",
            "remove_command",
            "sync_python",
            "sync_python_command",
        ),
        EnvironmentMonitor: ("snapshot", "snapshot_command", "watch"),
        PostgresCluster: (
            "approve_image",
            "approve_image_command",
            "ensure_running",
            "ensure_running_command",
            "from_project",
            "resolve_image_digest",
            "resolve_image_digest_command",
            "resource_snapshot",
            "resource_snapshot_command",
            "status",
            "status_command",
            "stop",
            "stop_command",
            "to_diagnostic_dict",
        ),
        DatabaseResource: (
            "backup",
            "backup_command",
            "current",
            "current_command",
            "drop",
            "drop_command",
            "exists",
            "exists_command",
            "list",
            "names",
            "reset_admin_password",
            "reset_admin_password_command",
            "restore",
            "restore_command",
        ),
        BackupResource: (
            "delete",
            "delete_command",
            "history",
            "latest",
            "list",
            "validate",
            "validate_command",
        ),
        DatabasePreparationCoordinator: (
            "prepare",
            "prepare_command",
            "refresh_database",
            "refresh_database_command",
        ),
        OdooInstance: (
            "iter_logs",
            "run",
            "run_command",
            "run_foreground",
            "run_foreground_command",
            "run_shell_script",
            "run_shell_script_command",
            "shell",
            "shell_command",
            "start",
            "start_command",
            "status",
            "stop",
            "stop_command",
            "wait_ready",
        ),
        BackupCatalog: (
            "active_environment_for",
            "add_environment_event",
            "clear_environment_runtime",
            "close",
            "create_environment",
            "distinct_restored_database_names",
            "fail_download",
            "get_backup_history",
            "get_by_id",
            "get_copy_journal",
            "get_environment",
            "get_environment_runtime",
            "has_tracked_database",
            "latest_backup",
            "latest_restore",
            "latest_restore_provenance",
            "list_backups",
            "list_environment_runtimes",
            "list_environments",
            "list_environments_with_runtimes",
            "record_database_dropped",
            "record_deletion",
            "record_environment_use",
            "record_restore",
            "record_validation",
            "start_download",
            "success_download",
            "update_environment",
            "update_environment_state",
            "update_path",
            "upsert_copy_journal",
            "upsert_environment_runtime",
            "verify_identity",
        ),
    }

    for cls, names in expected.items():
        discovered = tuple(
            name
            for name, value in inspect.getmembers(cls, inspect.isroutine)
            if not name.startswith("_")
        )
        assert discovered == names


def _assert_envelope_keys(envelope: dict[str, object], *, success: bool) -> None:
    common = {"schema_version", "ok", "command", "context", "provenance", "dry_run", "warnings"}
    expected = common | ({"result", "data"} if success else {"error"})
    assert set(envelope) == expected
    assert envelope["schema_version"] == 1
    assert envelope["ok"] is success
    assert envelope["context"] == {}
    assert envelope["warnings"] == []
    if success:
        assert envelope["result"] == envelope["data"]


def test_json_success_envelope_v1_is_complete_and_result_equals_data(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--no-input",
            "--dry-run",
            "--json",
            "--odoo-bin",
            "/opt/odoo/odoo-bin",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    envelope = json.loads(result.stdout)
    _assert_envelope_keys(envelope, success=True)
    assert envelope["command"] == "init"
    assert envelope["dry_run"] is True
    assert envelope["provenance"] == {
        "option": ["odoo_bin"],
        "vscode": [],
        "discovery": [],
        "default": [],
    }


def test_json_failure_envelope_v1_is_complete_and_sanitized() -> None:
    diagnostic = "password='quoted secret' token=token-secret /private/runtime/secret-path"
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        side_effect=RuntimeError(diagnostic),
    ):
        result = CliRunner().invoke(cli, ["eval", "1", "--json"])

    assert result.exit_code == 1
    assert result.stderr == ""
    envelope = json.loads(result.stdout)
    _assert_envelope_keys(envelope, success=False)
    assert envelope["command"] == "eval"
    assert envelope["error"]["code"] == "eval_failed"
    assert "quoted secret" not in result.stdout
    assert "token-secret" not in result.stdout
    assert "/private/runtime/secret-path" not in result.stdout


def test_non_json_failure_is_sanitized_to_stderr_only() -> None:
    diagnostic = "password='quoted secret' /private/runtime/secret-path"
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        side_effect=RuntimeError(diagnostic),
    ):
        result = CliRunner().invoke(cli, ["eval", "1"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "quoted secret" not in result.stderr
    assert "/private/runtime/secret-path" not in result.stderr


def test_click_parse_failure_remains_native_usage_error() -> None:
    result = CliRunner().invoke(cli, ["eval", "1", "--json", "--not-an-option"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Error: No such option" in result.stderr
    assert "Usage:" in result.stderr


@pytest.mark.parametrize("command", ["run", "shell"])
def test_passthrough_commands_forward_child_exit_code(command: str) -> None:
    instance = MagicMock()
    child_exit = 17 if command == "run" else 23
    method = instance.run_foreground if command == "run" else instance.shell
    method.return_value = child_exit

    with patch("odoo_instance_sdk.cli.cli_context._check_port_free", return_value=True):
        result = _passthrough_instance(
            instance, [command, "--", "--dev"] if command == "shell" else [command]
        )

    assert result.exit_code == child_exit
    method.assert_called_once_with(**({"args": ["--dev"]} if command == "shell" else {}))
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("command", ["run", "shell"])
def test_passthrough_commands_map_keyboard_interrupt_to_130(command: str) -> None:
    instance = MagicMock()
    method = instance.run_foreground if command == "run" else instance.shell
    method.side_effect = KeyboardInterrupt

    with patch("odoo_instance_sdk.cli.cli_context._check_port_free", return_value=True):
        result = _passthrough_instance(instance, [command])

    assert result.exit_code == 130
    assert result.stdout == ""
    assert result.stderr == ""


def test_passthrough_run_and_shell_preserve_native_streams() -> None:
    run_instance = MagicMock()

    def run_foreground() -> int:
        sys.stdout.write("run stdout")
        sys.stderr.write("run stderr")
        return 0

    run_instance.run_foreground.side_effect = run_foreground
    with patch("odoo_instance_sdk.cli.cli_context._check_port_free", return_value=True):
        run_result = _passthrough_instance(run_instance, ["run"])
    assert run_result.stdout == "run stdout"
    assert run_result.stderr == "run stderr"

    shell_instance = MagicMock()
    received_stdin: list[str] = []

    def shell(*, args: list[str]) -> int:
        received_stdin.append(sys.stdin.read())
        sys.stdout.write("shell stdout")
        sys.stderr.write("shell stderr")
        assert args == ["--dev"]
        return 0

    shell_instance.shell.side_effect = shell
    shell_result = _passthrough_instance(
        shell_instance,
        ["shell", "--", "--dev"],
        input_text="shell stdin",
    )
    assert shell_result.stdout == "shell stdout"
    assert shell_result.stderr == "shell stderr"
    assert received_stdin == ["shell stdin"]


def test_logs_follow_preserves_raw_stdout_and_streaming_arguments() -> None:
    instance = MagicMock()
    raw_lines = ["first\n", "\x1b[31msecond\x1b[0m\n", "unterminated"]
    instance.iter_logs.return_value = iter(raw_lines)

    result = _passthrough_instance(instance, ["logs", "--follow", "--tail", "2"])

    assert result.exit_code == 0
    assert result.stdout == "".join(raw_lines)
    assert result.stderr == ""
    instance.iter_logs.assert_called_once_with(tail=2, follow=True)


def test_outside_project_all_projects_listing_does_not_require_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    empty = Snapshot(schema_version=3, generated_at=datetime.now(UTC), projects=(), environments=())
    client = MagicMock()
    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch(
            "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot", return_value=empty
        ) as snapshot,
    ):
        result = CliRunner().invoke(cli, ["env", "list", "--all-projects", "--json"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["provenance"] == {
        "project_source": "null",
        "environment_source": "null",
    }
    snapshot.assert_called_once_with(project_id=None, include_removed=False)
