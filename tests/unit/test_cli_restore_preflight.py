from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from click.testing import CliRunner

from odoo_instance_sdk.cli import cli

if TYPE_CHECKING:
    import pytest


def _local_archive(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", '{"db_name":"local_db"}')
        archive.writestr("dump.sql", "select 'local';\n")
        archive.writestr("filestore/local_db/marker", b"local")
    return path


def _write_restore_config(path: Path, data_dir: str) -> None:
    path.write_text(
        "[options]\n"
        "http_interface = 127.0.0.1\n"
        "http_port = 8069\n"
        "db_host = 127.0.0.1\n"
        "db_port = 5432\n"
        "db_user = odoo\n"
        "db_password = private\n"
        f"data_dir = {data_dir}\n"
    )


def test_restore_dry_run_local_archive_has_existing_and_missing_data_dir_parity(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
) -> None:
    archive_path = _local_archive(project_manifest / "caller-owned.zip")
    source_config = project_manifest / "odoo.conf"
    restore_root = project_manifest / "restore"
    existing = project_manifest / "restore" / "existing" / "data"
    missing = project_manifest / "restore" / "missing" / "nested" / "data"
    existing.mkdir(parents=True)
    observed: list[Path] = []

    def disk_usage(path: Path) -> SimpleNamespace:
        observed.append(path.resolve())
        return SimpleNamespace(free=2 * 1024**3)

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: project_manifest
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: "/usr/bin/psql"
    )
    monkeypatch.setattr(shutil, "disk_usage", disk_usage)

    payloads = []
    for data_dir, should_exist in ((existing, True), (missing, False)):
        _write_restore_config(source_config, data_dir.relative_to(project_manifest).as_posix())
        result = CliRunner().invoke(
            cli,
            [
                "db",
                "restore",
                "--file",
                str(archive_path),
                "--target",
                "demo_copy",
                "--dry-run",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["dry_run"] is True
        assert document["ok"] is True
        payloads.append(document["result"])
        assert data_dir.exists() is should_exist

    assert [step["step_id"] for step in payloads[0]["steps"]] == [
        step["step_id"] for step in payloads[1]["steps"]
    ]
    assert payloads[0]["warnings"] == payloads[1]["warnings"]
    assert payloads[0]["fingerprint"]
    assert payloads[1]["fingerprint"]
    assert set(observed) == {existing.resolve(), restore_root.resolve()}


def test_restore_dry_run_local_archive_reports_real_disk_inspection_failure_once(
    monkeypatch: pytest.MonkeyPatch,
    project_manifest: Path,
) -> None:
    archive_path = _local_archive(project_manifest / "caller-owned.zip")
    source_config = project_manifest / "odoo.conf"
    missing = project_manifest / "restore" / "missing" / "nested" / "data"
    _write_restore_config(source_config, missing.relative_to(project_manifest).as_posix())

    def fail_disk_usage(_path: Path) -> object:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(
        "odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: project_manifest
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: "/usr/bin/psql"
    )
    monkeypatch.setattr(shutil, "disk_usage", fail_disk_usage)

    result = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            "--file",
            str(archive_path),
            "--target",
            "demo_copy",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    document = json.loads(result.stdout)
    assert document["ok"] is False
    assert document["error"]["code"] == "backup_disk_inspection"
    assert document["error"]["details"] == {
        "requested_path": str(missing.resolve()),
        "inspection_path": str(project_manifest.resolve()),
        "reason": "Permission denied",
    }
    assert result.stdout.count('"error"') == 1
    assert not missing.exists()


def test_restore_dry_run_emits_one_disk_inspection_failure_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from odoo_instance_sdk.exceptions import BackupDiskInspectionError

    client = MagicMock()
    client.environments.refresh_database_command.side_effect = BackupDiskInspectionError(
        requested_path=str(tmp_path / "missing" / "data"),
        inspection_path=str(tmp_path),
        reason="Permission denied",
    )
    monkeypatch.setattr("odoo_instance_sdk.commands.db.resolve_project_path", lambda _ctx: tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.commands.db.OdooClient", lambda **_: client)

    result = CliRunner().invoke(
        cli,
        [
            "db",
            "restore",
            "00000000-0000-0000-0000-000000000007",
            "--target",
            "demo_copy",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "backup_disk_inspection"
    assert "disk inspection" in payload["error"]["message"]
    assert result.stdout.count('"error"') == 1
    client.environments.refresh_database_command.assert_called_once()
