from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner, Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.models import (
    DatabasePreparationAction,
    DatabasePreparationResult,
    PostRestoreCheckpoint,
    RestoreCompletionFailureContext,
    RestoreCompletionResult,
)


def _command(value: object = None, *, error: BaseException | None = None) -> Command[object]:
    def run(_context: object) -> object:
        if error is not None:
            raise error
        return value

    return Command.create(ExecutionPlan(), run)


@dataclass(frozen=True)
class _RestoreFormatFailureCase:
    id: str
    content: bytes
    expected_error: dict[str, object]


_RESTORE_FORMAT_FAILURES = (
    _RestoreFormatFailureCase(
        "postgres-custom",
        b"PGDMP\x01caller-owned",
        {
            "code": "backup_unsupported_format",
            "message": "unsupported local backup format: PostgreSQL custom dump",
            "details": {"format": "postgres_custom_dump"},
        },
    ),
    _RestoreFormatFailureCase(
        "malformed-zip-central-directory",
        b"PK\x01\x02truncated",
        {
            "code": "backup_corrupt",
            "message": "backup archive is corrupt",
            "details": {"errors": ["Not a valid ZIP file"]},
        },
    ),
    _RestoreFormatFailureCase(
        "unknown",
        b"unknown caller-owned bytes",
        {
            "code": "backup_unknown_format",
            "message": "unrecognized local backup format",
            "details": {"format": "unknown"},
        },
    ),
)


def _write_valid_restore_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as payload:
        payload.writestr("manifest.json", '{"db_name":"local_db"}')
        payload.writestr("dump.sql", "SELECT 1;\n")
        payload.writestr("filestore/local_db/marker", b"local")


def _restore_file_dry_run_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    archive: Path,
    effects: list[str],
) -> Result:
    client = MagicMock()

    def build_command(_project: object, **kwargs: object) -> Command[object]:
        from odoo_instance_sdk.internal.database_preparation import capture_local_archive_restore

        capture_local_archive_restore(
            kwargs["restore_source"],  # type: ignore[arg-type]
            snapshot_directory=tmp_path / ".odcli" / "restore",
        )
        effects.append("database")
        return _command(DatabasePreparationResult(mode=DatabasePreparationAction.RESTORE))

    client.environments.refresh_database_command.side_effect = build_command
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    return CliRunner().invoke(
        cli,
        ["db", "restore", "--file", str(archive), "--dry-run", "--format", "json"],
    )


def test_restore_file_dry_run_accepts_valid_local_zip_before_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "caller-owned.input"
    _write_valid_restore_archive(archive)
    original = archive.read_bytes()
    effects: list[str] = []

    result = _restore_file_dry_run_command(monkeypatch, tmp_path, archive, effects)

    assert archive.read_bytes() == original
    assert str(archive) not in result.output
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"] is True
    assert effects == ["database"]


def test_restore_completion_dry_run_uses_existing_leaf_and_is_inert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = MagicMock()
    effects: list[str] = []
    result_value = RestoreCompletionResult(
        target_database="restored_db",
        checkpoint=PostRestoreCheckpoint.RESET_PENDING,
        completed_stages=(),
        pending_stage="admin_reset",
        requested_modules=("base", "mail"),
        previous_default="old_default",
        effective_default="restored_db",
        published=False,
    )
    client.environments.complete_restore_command.return_value = Command.create(
        ExecutionPlan(
            observations=({"target_database": "restored_db", "checkpoint": "reset_pending"},)
        ),
        lambda _context: _record_completion_effect(effects, result_value),
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.admin_password.resolve_admin_password_secret",
        lambda **_: ("private-secret", "process"),
    )

    invocation = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            "--complete",
            "restored_db",
            "--update-module",
            "base",
            "--update-module",
            "mail",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert invocation.exit_code == 0, invocation.output
    document = json.loads(invocation.stdout)
    assert document["ok"] is True
    completion_plan = document["result"]["observations"][0]
    assert completion_plan["target_database"] == "restored_db"
    assert completion_plan["checkpoint"] == "reset_pending"
    assert "private-secret" not in invocation.output
    client.environments.complete_restore_command.assert_called_once()
    assert client.environments.complete_restore_command.call_args.args == (tmp_path, "restored_db")
    assert client.environments.complete_restore_command.call_args.kwargs["update_modules"] == (
        "base",
        "mail",
    )
    assert effects == []


@pytest.mark.parametrize("output_format", ["json", "toon"])
def test_restore_completion_interrupt_preserves_typed_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output_format: str,
) -> None:
    client = MagicMock()
    interrupted = KeyboardInterrupt()
    setattr(
        interrupted,
        "failure_context",
        RestoreCompletionFailureContext(
            target_database="restored_db",
            checkpoint=PostRestoreCheckpoint.RESET_COMPLETE,
            completed_stages=("module_update",),
            pending_stage="default_switch",
            requested_modules=("base", "mail"),
            previous_default="old_default",
            effective_default="old_default",
            published=False,
        ),
    )
    client.environments.complete_restore_command.return_value = _command(error=interrupted)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)

    invocation = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            "--complete",
            "restored_db",
            "--yes",
            "--format",
            output_format,
        ],
    )

    assert invocation.exit_code == 130, invocation.output
    if output_format == "json":
        context = json.loads(invocation.stdout)["context"]
        assert context["target_database"] == "restored_db"
        assert context["completed_stages"] == ["module_update"]
        assert context["pending_stage"] == "default_switch"
        assert context["requested_modules"] == ["base", "mail"]
        assert context["previous_default"] == "old_default"
        assert context["effective_default"] == "old_default"
    else:
        assert "restored_db" in invocation.stdout
        assert "module_update" in invocation.stdout
        assert "default_switch" in invocation.stdout
        assert "old_default" in invocation.stdout
    assert "private-secret" not in invocation.stdout


def _record_completion_effect(
    effects: list[str], result: RestoreCompletionResult
) -> RestoreCompletionResult:
    effects.append("executed")
    return result


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.id) for case in _RESTORE_FORMAT_FAILURES],
)
def test_restore_file_dry_run_rejects_local_formats_before_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _RestoreFormatFailureCase,
) -> None:
    archive = tmp_path / "caller-owned.input"
    archive.write_bytes(case.content)
    original = archive.read_bytes()
    effects: list[str] = []

    result = _restore_file_dry_run_command(monkeypatch, tmp_path, archive, effects)

    assert result.exit_code == 1, result.output
    assert archive.read_bytes() == original
    assert str(archive) not in result.output
    assert json.loads(result.stdout)["error"] == case.expected_error
    assert effects == []
