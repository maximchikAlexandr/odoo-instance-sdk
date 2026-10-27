from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from odcli_multica.cli import cli
from odcli_multica.models import MergeRequestPublicationResult
from toon import DecodeOptions, decode

from odoo_instance_sdk.commands.output import (
    OutputMode,
    emit,
    failure_document,
    model_to_dict,
    success_document,
)


def test_merge_request_help_explains_configured_assignee_override() -> None:
    result = CliRunner().invoke(cli, ["gitlab", "mr", "publish", "--help"])

    assert result.exit_code == 0, result.output
    help_text = " ".join(result.output.replace("│", " ").split()).lower()
    assert "gitlab.mr.default_assignee" in help_text
    assert "numeric id or exact username" in help_text
    assert "assignee_id" in help_text
    assert "not reviewers" in help_text


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


@pytest.mark.parametrize(
    ("mode", "expected_fragments"),
    [
        pytest.param(OutputMode.RICH, ("assignee_id", "assignee_username", "alice"), id="rich"),
        pytest.param(OutputMode.JSON, ("assignee_id", "assignee_username", "alice"), id="json"),
        pytest.param(OutputMode.TOON, ("assignee_id", "assignee_username", "alice"), id="toon"),
    ],
)
def test_merge_request_assignee_result_is_safe_in_every_output_mode(
    mode: OutputMode, expected_fragments: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    result = MergeRequestPublicationResult(
        merge_request_id=7,
        web_url="https://gitlab.example/team/repo/-/merge_requests/7",
        source_branch="feature",
        target_branch="main",
        outcome="created",
        assignee_id=17,
        assignee_username="alice",
        assignee_source="default",
    )
    emit(
        success_document(command="gitlab.mr.publish", result=model_to_dict(result)),
        mode,
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "secret-token" not in output
    assert all(fragment in output for fragment in expected_fragments)
    assert "17" in output
    assert "default" in output


def test_cli_is_bounded_to_context_and_environment_prepare() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "context" in result.output
    assert "env" in result.output
