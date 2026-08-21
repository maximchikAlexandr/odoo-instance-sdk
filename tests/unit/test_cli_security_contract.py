from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli


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


@pytest.mark.parametrize("command, method", [("run", "run_foreground"), ("shell", "shell")])
def test_run_and_shell_sanitize_runtime_exception(command: str, method: str) -> None:
    runner = CliRunner()
    instance = MagicMock()
    getattr(instance, method).side_effect = RuntimeError("password=top-secret /private/path")
    client = MagicMock()
    client.instance.from_environment.return_value = instance
    env = SimpleNamespace(http_interface="127.0.0.1", http_port=8069)
    with (
        patch("odoo_instance_sdk.cli._make_client", return_value=client),
        patch("odoo_instance_sdk.cli._resolve_ready_env", return_value=env),
        patch("odoo_instance_sdk.cli._check_port_free", return_value=True),
    ):
        result = runner.invoke(cli, [command])

    assert result.exit_code == 1
    assert "top-secret" not in result.output
    assert "/private/path" not in result.output
