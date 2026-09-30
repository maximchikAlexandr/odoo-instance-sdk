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
from odoo_instance_sdk.models import DatabasePreparationAction, DatabasePreparationResult


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
