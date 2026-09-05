from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import msgspec
import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue, SemanticPlanObservation
from odoo_instance_sdk.internal.pg.drop import (
    DatabaseDropResult,
    DatabaseDropSafetyError,
    DatabaseDropSession,
    _decode_inspection,
)
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    ProcessResult,
    RunContext,
)

_RAW_SESSION_CASES: tuple[dict[str, object], ...] = (
    {"pid": 7, "user": "Bearer bearer-session-sentinel"},
    {"pid": 8, "user": "odoo", "client": "Basic basic-session-sentinel"},
    {"pid": 9, "user": "odoo", "application": "eyJjwt-session-sentinel.payload.signature"},
    {"pid": 10, "user": "odoo", "application": "Authorization: header-session-sentinel"},
    {"pid": 11, "user": "odoo\n\x1b[31m", "application": "test\x00client"},
)

_EXPECTED_SESSION_CASES: tuple[dict[str, JsonValue], ...] = (
    {"pid": 7, "user": "<redacted>", "client": None, "application": None},
    {"pid": 8, "user": "odoo", "client": "<redacted>", "application": None},
    {"pid": 9, "user": "odoo", "client": None, "application": "<redacted>"},
    {
        "pid": 10,
        "user": "odoo",
        "client": None,
        "application": "Authorization: <redacted>",
    },
    {
        "pid": 11,
        "user": r"odoo\x0a\x1b[31m",
        "client": None,
        "application": r"test\x00client",
    },
)

_INSPECTION_RESULT = ProcessResult(
    argv=("psql",),
    returncode=0,
    stdout=json.dumps({"exists": True, "is_template": False, "sessions": _RAW_SESSION_CASES}),
    stderr="",
    duration=0.0,
    cwd=None,
    environment=(),
)


def _command(
    *,
    sessions: tuple[DatabaseDropSession, ...] = (),
    error: BaseException | None = None,
) -> Command[DatabaseDropResult]:
    action = PreparedAction("database.drop")
    projected_sessions = tuple(
        cast("dict[str, JsonValue]", msgspec.to_builtins(session)) for session in sessions
    )

    def run(context: RunContext[DatabaseDropResult]) -> DatabaseDropResult:
        context.action("database.drop")
        if error is not None:
            raise error
        return DatabaseDropResult(database="feature_db", cluster="127.0.0.1:5432")

    return Command.create(
        ExecutionPlan(
            steps=(action.public_projection(),),
            observations=(
                SemanticPlanObservation(
                    kind="semantic",
                    goal="Drop database feature_db",
                    active_sessions=projected_sessions,
                ),
            ),
        ),
        run,
        (action,),
    )


@pytest.mark.unit
@pytest.mark.parametrize("machine_options", [("--json",), ("--format", "toon")])
def test_machine_drop_requires_yes_before_sdk_or_transport(
    machine_options: tuple[str, ...],
) -> None:
    args = ["db", "drop", "feature_db", *machine_options]
    with patch("odoo_instance_sdk.commands.pg._database_instance") as resolve:
        result = CliRunner().invoke(cli, args)

    assert result.exit_code == 1, result.output
    assert result.output.count("schema_version") == 1
    assert "confirmation_required" in result.output
    resolve.assert_not_called()


@pytest.mark.unit
def test_machine_drop_yes_executes_one_document_without_confirmation(
    project_manifest: Path,
) -> None:
    instance = SimpleNamespace(_postgres_cluster=SimpleNamespace(endpoint="127.0.0.1:5432"))
    command = _command()
    with (
        patch("odoo_instance_sdk.commands.pg._database_instance", return_value=(None, instance)),
        patch(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
            return_value=command,
        ) as build,
    ):
        result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest),
                "db",
                "drop",
                "feature_db",
                "--yes",
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.output.count('"schema_version"') == 1
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["result"]["database"] == "feature_db"
    build.assert_called_once()


@pytest.mark.unit
def test_machine_drop_dry_run_does_not_require_yes_or_execute(project_manifest: Path) -> None:
    instance = SimpleNamespace(_postgres_cluster=SimpleNamespace(endpoint="127.0.0.1:5432"))
    command = _command()
    with (
        patch("odoo_instance_sdk.commands.pg._database_instance", return_value=(None, instance)),
        patch(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
            return_value=command,
        ),
    ):
        result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest),
                "db",
                "drop",
                "feature_db",
                "--dry-run",
                "--json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["result"]["steps"][0]["step_id"] == "database.drop"


@pytest.mark.unit
@pytest.mark.parametrize("format_args", [(), ("--json",), ("--format", "toon")])
def test_drop_dry_run_projects_typed_active_sessions_in_every_transport(
    project_manifest: Path, format_args: tuple[str, ...]
) -> None:
    typed_sessions = _decode_inspection(_INSPECTION_RESULT, "feature_db").sessions
    instance = SimpleNamespace(_postgres_cluster=SimpleNamespace(endpoint="127.0.0.1:5432"))
    command = _command(sessions=typed_sessions)

    with (
        patch("odoo_instance_sdk.commands.pg._database_instance", return_value=(None, instance)),
        patch(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
            return_value=command,
        ),
    ):
        result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest),
                "db",
                "drop",
                "feature_db",
                "--dry-run",
                *format_args,
            ],
        )

    assert result.exit_code == 0, result.output
    expected = list(_EXPECTED_SESSION_CASES)
    if format_args == ():
        assert "Active sessions:" in result.output
        assert "pid=7, user=<redacted>" in result.output
    elif format_args == ("--json",):
        assert json.loads(result.stdout)["result"]["observations"][0]["active_sessions"] == expected
    else:
        from toon import DecodeOptions, decode

        payload = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert payload["result"]["observations"][0]["active_sessions"] == expected
    for sentinel in (
        "bearer-session-sentinel",
        "basic-session-sentinel",
        "jwt-session-sentinel",
        "header-session-sentinel",
    ):
        assert sentinel not in result.output


@pytest.mark.unit
@pytest.mark.parametrize("format_args", [(), ("--json",), ("--format", "toon")])
def test_drop_refusal_projects_active_sessions_as_failure_details(
    project_manifest: Path, format_args: tuple[str, ...]
) -> None:
    typed_sessions = _decode_inspection(_INSPECTION_RESULT, "feature_db").sessions
    instance = SimpleNamespace(_postgres_cluster=SimpleNamespace(endpoint="127.0.0.1:5432"))
    command = _command(
        sessions=typed_sessions,
        error=DatabaseDropSafetyError("active sessions require force", typed_sessions),
    )

    with (
        patch("odoo_instance_sdk.commands.pg._database_instance", return_value=(None, instance)),
        patch(
            "odoo_instance_sdk.internal.pg.drop.build_database_drop_command",
            return_value=command,
        ),
    ):
        result = CliRunner().invoke(
            cli,
            [
                "--project",
                str(project_manifest),
                "db",
                "drop",
                "feature_db",
                "--yes",
                *format_args,
            ],
        )

    assert result.exit_code == 1, result.output
    expected = list(_EXPECTED_SESSION_CASES)
    if format_args == ():
        assert "active sessions" in result.output
        assert '"pid":7' in result.output
    elif format_args == ("--json",):
        assert json.loads(result.stdout)["error"]["details"] == {"active_sessions": expected}
    else:
        from toon import DecodeOptions, decode

        payload = decode(result.stdout, DecodeOptions(indent=2, strict=True))
        assert payload["error"]["details"] == {"active_sessions": expected}
    for sentinel in (
        "bearer-session-sentinel",
        "basic-session-sentinel",
        "jwt-session-sentinel",
        "header-session-sentinel",
    ):
        assert sentinel not in result.output


@pytest.mark.unit
def test_drop_help_exposes_safety_controls() -> None:
    result = CliRunner().invoke(cli, ["db", "drop", "--help"])

    assert result.exit_code == 0, result.output
    for option in ("--force-default", "--force-connections", "--yes", "--dry-run"):
        assert option in result.output
