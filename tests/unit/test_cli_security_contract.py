from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.resources.environment import EnvironmentState
from tests.unit.monitor_support import FakeProcessProvider


@pytest.fixture(autouse=True)
def _inject_monitor_process_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    original_init = EnvironmentMonitor.__init__

    def init(self: EnvironmentMonitor, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("process_provider", FakeProcessProvider())
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EnvironmentMonitor, "__init__", init)


@pytest.mark.parametrize(
    "diagnostic, secret",
    [
        ("password='quoted secret' passwd=bare-secret", "quoted secret"),
        ('api_key="api-secret" token=token-secret', "api-secret"),
        ("postgresql://user:dsn-secret@db.example/app", "dsn-secret"),
        ("https://alice:auth-secret@example.test/callback", "auth-secret"),
        ("/private/runtime/secret-path\\n" + "x" * 3000, "secret-path"),
    ],
)
def test_eval_json_failure_is_single_sanitized_envelope(diagnostic: str, secret: str) -> None:
    runner = CliRunner()
    with patch(
        "odoo_instance_sdk.internal.context.resolve_environment",
        side_effect=RuntimeError(diagnostic),
    ):
        result = runner.invoke(cli, ["eval", "1", "--json"])

    assert result.exit_code == 1
    assert result.stderr == ""
    assert result.output.count('"schema_version"') == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["command"] == "eval"
    assert envelope["context"] == {}
    assert envelope["dry_run"] is False
    assert envelope["error"]["code"] == "eval_failed"
    assert secret not in result.output
    assert len(envelope["error"]["message"]) <= 2000


def test_init_json_failure_is_single_stdout_envelope(tmp_path: pytest.TempPathFactory) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--no-input", "--json", "--project", str(tmp_path)])

    assert result.exit_code == 1
    assert result.stderr == ""
    assert result.output.count('"schema_version"') == 1
    envelope = json.loads(result.output)
    assert envelope == {
        "schema_version": 1,
        "ok": False,
        "command": "init",
        "context": {},
        "provenance": {},
        "dry_run": False,
        "warnings": [],
        "error": {"code": "init_failed", "message": "Missing required option --odoo-bin"},
    }


def test_init_json_success_has_stable_result_and_provenance(
    tmp_path: pytest.TempPathFactory,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
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
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["command"] == "init"
    assert envelope["dry_run"] is True
    assert envelope["provenance"]["option"] == ["odoo_bin"]
    assert envelope["result"] == envelope["data"]


@pytest.mark.parametrize(
    "command, method",
    [("run", "run_foreground"), ("shell", "shell"), ("logs", "iter_logs")],
)
def test_run_and_shell_sanitize_runtime_exception(command: str, method: str) -> None:
    runner = CliRunner()
    instance = MagicMock()
    getattr(instance, method).side_effect = RuntimeError("password=top-secret /private/path")
    client = MagicMock()
    client.instance.from_environment.return_value = instance
    env = SimpleNamespace(
        state=EnvironmentState.READY,
        http_interface="127.0.0.1",
        http_port=8069,
    )
    with (
        patch("odoo_instance_sdk.commands.context.OdooClient", return_value=client),
        patch("odoo_instance_sdk.internal.context.resolve_environment", return_value=env),
        patch("odoo_instance_sdk.internal.context._verify_env_runtime", return_value=None),
        patch("odoo_instance_sdk.internal.context._check_port_free", return_value=True),
    ):
        result = runner.invoke(cli, [command])

    assert result.exit_code == 1
    assert "top-secret" not in result.output
    assert "/private/path" not in result.output


def test_env_list_is_read_only_through_public_resources() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.backups.list.return_value = []
    # New contract: env list delegates to EnvironmentMonitor.snapshot(); patch
    # it to avoid the real catalog and assert the CLI body itself does not
    # touch client.get_catalog() or write environment events.
    from datetime import UTC, datetime

    from odoo_instance_sdk.models import Snapshot

    empty_snapshot = Snapshot(
        schema_version=3, generated_at=datetime.now(UTC), projects=(), environments=()
    )
    with (
        patch("odoo_instance_sdk.commands.env.OdooClient", return_value=client),
        patch(
            "odoo_instance_sdk.commands.env.EnvironmentMonitor.snapshot",
            return_value=empty_snapshot,
        ),
    ):
        result = runner.invoke(cli, ["env", "list", "--all-projects", "--json"])

    assert result.exit_code == 0, result.output
    # The CLI body no longer calls client.environments.list / client.backups.list
    # for the JSON path (the monitor owns discovery). It must not open the
    # catalog through the client or write environment events.
    client.environments.list.assert_not_called()
    client.get_catalog.assert_not_called()
    client.environments.record_use.assert_not_called()


def test_run_records_use_once_before_foreground_start() -> None:
    client = MagicMock()
    instance = MagicMock()
    instance.run_foreground.return_value = 0
    env = SimpleNamespace(http_interface="127.0.0.1", http_port=8069)
    calls = MagicMock()
    calls.attach_mock(client.environments.record_use, "record_use")
    calls.attach_mock(instance.run_foreground, "run_foreground")
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance", return_value=(client, env, instance)
        ),
        patch("odoo_instance_sdk.cli.cli_context._check_port_free", return_value=True),
    ):
        result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 0, result.output
    assert calls.mock_calls == [
        call.record_use(env),
        call.run_foreground(),
    ]


def test_port_conflict_does_not_record_use_or_start_foreground() -> None:
    client = MagicMock()
    instance = MagicMock()
    env = SimpleNamespace(http_interface="127.0.0.1", http_port=8069)
    with (
        patch(
            "odoo_instance_sdk.cli.cli_context.ready_instance", return_value=(client, env, instance)
        ),
        patch("odoo_instance_sdk.cli.cli_context._check_port_free", return_value=False),
    ):
        result = CliRunner().invoke(cli, ["run"])

    assert result.exit_code == 1
    client.environments.record_use.assert_not_called()
    instance.run_foreground.assert_not_called()


def test_shell_does_not_record_use() -> None:
    client = MagicMock()
    instance = MagicMock()
    instance.shell.return_value = 0
    with patch(
        "odoo_instance_sdk.cli.cli_context.ready_instance",
        return_value=(client, SimpleNamespace(), instance),
    ):
        result = CliRunner().invoke(cli, ["shell"])

    assert result.exit_code == 0, result.output
    client.environments.record_use.assert_not_called()
