from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from click.testing import CliRunner
from multica_py import CommandCancelledError
from odcli_multica.cli import cli

from odoo_instance_sdk.commands.output import action_command

if TYPE_CHECKING:
    import pytest
    from click.testing import Result
    from odcli_multica import ContextRequest, VerifiedTaskContext

    from odoo_instance_sdk import Command


class _CliClient:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    def context_command(self, _request: ContextRequest) -> Command[VerifiedTaskContext]:
        if self.error is not None:
            raise self.error
        return cast(
            "Command[VerifiedTaskContext]",
            action_command(
                "test.context",
                lambda: cast("VerifiedTaskContext", SimpleNamespace()),
                description="test context",
            ),
        )


def _invoke_context(
    monkeypatch: pytest.MonkeyPatch, client: _CliClient, tmp_path: Path, *extra: str
) -> Result:
    module = importlib.import_module("odcli_multica.cli")

    def fake_client(_profile: str | None, _workspace_id: str | None, _odoo_bin: str) -> _CliClient:
        return client

    monkeypatch.setattr(module, "_client", fake_client)
    checkout = tmp_path / "checkout"
    checkout.mkdir(exist_ok=True)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return CliRunner().invoke(
        cli,
        [
            "context",
            str(checkout),
            "--project",
            str(project),
            "--multica-project",
            "project",
            "--issue",
            "issue",
            "--run",
            "run",
            "--repository-url",
            "https://example.test/repo",
            "--format",
            "json",
            *extra,
        ],
    )


def test_cli_dry_run_emits_one_machine_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _invoke_context(monkeypatch, _CliClient(), tmp_path, "--dry-run")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["dry_run"] is True


def test_cli_error_and_interruption_are_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = _invoke_context(monkeypatch, _CliClient(CommandCancelledError("cancelled")), tmp_path)
    assert failed.exit_code == 1
    assert json.loads(failed.output)["ok"] is False

    interrupted = _invoke_context(monkeypatch, _CliClient(KeyboardInterrupt()), tmp_path)
    assert interrupted.exit_code == 1
    assert json.loads(interrupted.output)["error"]["code"] == "interrupted"
