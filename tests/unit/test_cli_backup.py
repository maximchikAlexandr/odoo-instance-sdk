from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZipFile

from click.testing import CliRunner

if TYPE_CHECKING:
    import pytest
    from click.testing import Result

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.models import BackupState
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

BACKUP_ID = "00000000-0000-0000-0000-000000000007"


def _seed_backup(
    tmp_path: Path,
    *,
    fmt: str = "zip",
    valid_zip: bool = True,
    database_name: str = "demo",
) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "catalog.sqlite3"
    backup_path = tmp_path / f"backup.{fmt}"
    if fmt == "zip" and valid_zip:
        with ZipFile(backup_path, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"db_name": database_name}))
            archive.writestr("dump.sql", "-- test")
    else:
        backup_path.write_bytes(b"not-a-valid-archive")
    catalog = BackupCatalog(db_path=db_path)
    catalog.start_download(
        BACKUP_ID,
        "http://localhost:8069",
        database_name,
        fmt,
        True,
        backup_path,
    )
    catalog.success_download(BACKUP_ID, backup_path.name, backup_path.stat().st_size, "")
    catalog.close()
    return db_path, backup_path


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    args: list[str],
    *,
    input: str | None = None,
) -> Result:
    monkeypatch.setattr("odoo_instance_sdk.cli.get_catalog_path", lambda **_kwargs: db_path)
    return CliRunner().invoke(cli, args, input=input)


def test_backup_list_and_show_are_context_independent_and_format_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, backup_path = _seed_backup(tmp_path)
    json_result = _invoke(
        monkeypatch, db_path, ["backup", "list", "--all-projects", "--format", "json"]
    )
    toon_result = _invoke(
        monkeypatch, db_path, ["backup", "list", "--all-projects", "--format", "toon"]
    )
    assert json_result.exit_code == toon_result.exit_code == 0
    json_payload = json.loads(json_result.stdout)
    from toon import DecodeOptions, decode

    toon_payload = decode(toon_result.stdout, DecodeOptions(indent=2, strict=True))
    assert json_payload == toon_payload
    item = json_payload["result"]["backups"][0]
    assert item["id"] == BACKUP_ID
    assert item["file_present"] is True
    assert item["state"] == BackupState.AVAILABLE.value

    shown = _invoke(monkeypatch, db_path, ["backup", "show", BACKUP_ID, "--format", "json"])
    assert shown.exit_code == 0
    assert json.loads(shown.stdout)["result"]["path"] == str(backup_path)
    abbreviated = _invoke(
        monkeypatch, db_path, ["backup", "show", BACKUP_ID[:8], "--format", "json"]
    )
    assert abbreviated.exit_code == 1
    assert json.loads(abbreviated.stdout)["error"]["code"] == "backup_show_failed"


def test_backup_list_requires_project_or_explicit_global_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _backup_path = _seed_backup(tmp_path)
    refused = _invoke(monkeypatch, db_path, ["backup", "list", "--format", "json"])
    assert refused.exit_code == 1
    payload = json.loads(refused.stdout)
    assert payload["ok"] is False
    assert "run odcli init" in payload["error"]["message"]

    global_result = _invoke(
        monkeypatch, db_path, ["backup", "list", "--all-projects", "--format", "json"]
    )
    assert global_result.exit_code == 0
    assert json.loads(global_result.stdout)["provenance"] == {
        "project_source": "null",
        "environment_source": "null",
    }


def test_backup_delete_dry_run_and_machine_confirmation_gate_precede_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, backup_path = _seed_backup(tmp_path)
    required = _invoke(monkeypatch, db_path, ["backup", "delete", BACKUP_ID, "--format", "json"])
    assert required.exit_code == 1
    assert json.loads(required.stdout)["error"]["code"] == "confirmation_required"
    assert backup_path.is_file()

    preview = _invoke(
        monkeypatch,
        db_path,
        ["backup", "delete", BACKUP_ID, "--dry-run", "--format", "json"],
    )
    assert preview.exit_code == 0
    plan = json.loads(preview.stdout)["result"]["plan"]
    assert plan["backup_id"] == BACKUP_ID
    assert plan["path"] == str(backup_path)
    assert plan["state"] == BackupState.AVAILABLE.value
    assert backup_path.is_file()

    confirmed = _invoke(
        monkeypatch, db_path, ["backup", "delete", BACKUP_ID, "--yes", "--format", "json"]
    )
    assert confirmed.exit_code == 0
    assert backup_path.exists() is False
    catalog = BackupCatalog(db_path=db_path)
    row = catalog.get_by_id(BACKUP_ID)
    assert row is not None and row["state"] == BackupState.DELETED.value
    catalog.close()


def test_backup_delete_rich_confirmation_shows_immutable_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, backup_path = _seed_backup(tmp_path)
    result = _invoke(monkeypatch, db_path, ["backup", "delete", BACKUP_ID], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Delete plan:" in result.stdout
    assert str(backup_path) in result.stdout
    assert backup_path.exists() is False


def test_backup_validate_distinguishes_invalid_and_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid_db, _invalid_path = _seed_backup(tmp_path / "invalid", valid_zip=False)
    invalid = _invoke(
        monkeypatch, invalid_db, ["backup", "validate", BACKUP_ID, "--format", "json"]
    )
    assert invalid.exit_code == 1
    assert json.loads(invalid.stdout)["error"]["code"] == "backup_validate_invalid"

    unavailable_db, _dump_path = _seed_backup(tmp_path / "dump", fmt="dump")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    unavailable = _invoke(
        monkeypatch,
        unavailable_db,
        ["backup", "validate", BACKUP_ID, "--format", "json"],
    )
    assert unavailable.exit_code == 1
    assert json.loads(unavailable.stdout)["error"]["code"] == "backup_validate_unavailable"
