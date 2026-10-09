from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from odcli_multica.cli import cli
from toon import DecodeOptions, decode

from odoo_instance_sdk.commands.output import (
    OutputMode,
    emit,
    failure_document,
    success_document,
)


@pytest.mark.parametrize("mode", (OutputMode.JSON, OutputMode.TOON))
def test_machine_output_is_one_sanitized_document(
    mode: OutputMode, capsys: pytest.CaptureFixture[str]
) -> None:
    emit(
        success_document(
            command="context",
            result={"status": "verified", "checkout": "/task/checkout"},
        ),
        mode,
    )

    output = capsys.readouterr().out
    payload = (
        json.loads(output)
        if mode is OutputMode.JSON
        else decode(output, DecodeOptions(strict=True))
    )
    assert payload["ok"] is True
    assert payload["command"] == "context"
    assert payload["result"]["checkout"] == "/task/checkout"
    assert output.strip()


def test_rich_output_uses_the_same_sanitized_facts(capsys: pytest.CaptureFixture[str]) -> None:
    emit(
        failure_document(
            command="context",
            dry_run=False,
            error_message="token=secret-value",
        ),
        OutputMode.RICH,
    )

    output = capsys.readouterr().err
    assert "secret-value" not in output
    assert "<redacted>" in output
    assert "token" not in output


@pytest.mark.parametrize("mode", (OutputMode.JSON, OutputMode.TOON))
def test_machine_errors_redact_diagnostics(
    mode: OutputMode, capsys: pytest.CaptureFixture[str]
) -> None:
    emit(
        failure_document(command="context", dry_run=False, error_message="token=secret-value"),
        mode,
    )

    output = capsys.readouterr().out
    payload = (
        json.loads(output)
        if mode is OutputMode.JSON
        else decode(output, DecodeOptions(strict=True))
    )
    assert payload["error"]["message"] == "<redacted>"


def test_cli_is_bounded_to_context_and_environment_prepare() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "context" in result.output
    assert "env" in result.output
