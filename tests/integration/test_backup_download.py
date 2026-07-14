from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pytest_httpx import HTTPXMock

from odoo_instance_sdk.internal.paths import get_backups_dir


def test_successful_download(instance, tmp_path, monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        content=BACKUP_ZIP_CONTENT,
        headers={"content-disposition": 'attachment; filename="testdb_backup.zip"'},
    )

    backup = instance.databases.backup("testdb")
    assert backup.database_name == "testdb"
    assert backup.size_bytes == len(BACKUP_ZIP_CONTENT)
    assert backup.filename.endswith(".zip")
    assert "/" not in backup.filename
    assert Path(backup.path).is_file()


def test_backup_round_trips_through_catalog(instance, tmp_path, monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        content=BACKUP_ZIP_CONTENT,
        headers={"content-disposition": 'attachment; filename="testdb_backup.zip"'},
    )

    backup = instance.databases.backup("testdb")

    catalog = instance._client.get_catalog()
    backups = catalog.list_backups(source_base_url=instance.config.base_url, database_name="testdb")
    assert len(backups) == 1
    assert backups[0].id == backup.id
    assert backups[0].filename == backup.filename
    assert backups[0].size_bytes == backup.size_bytes
    assert backups[0].path == backup.path

    history = catalog.get_backup_history(backup_id=str(backup.id))
    kinds = [e.event_type.value for e in history]
    assert "download_started" in kinds
    assert "download_succeeded" in kinds


def test_interrupted_download(instance, tmp_path, monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)

    httpx_mock.add_exception(
        OSError("Connection lost"),
        url="http://localhost:8069/web/database/backup",
        method="POST",
    )

    with pytest.raises(Exception):
        instance.databases.backup("testdb")
    backup_dir = get_backups_dir()
    part_files = list(backup_dir.glob("*.part"))
    assert len(part_files) == 0

    rows = list(backup_dir.glob("*"))
    assert all(row.suffix != ".part" for row in rows)


def test_interrupted_download_audited_as_failed(
    instance, tmp_path, monkeypatch, httpx_mock: HTTPXMock
):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)

    httpx_mock.add_exception(
        OSError("Connection lost"),
        url="http://localhost:8069/web/database/backup",
        method="POST",
    )

    with pytest.raises(Exception):
        instance.databases.backup("testdb")

    catalog = instance._client.get_catalog()
    rows = catalog._conn.execute(
        "SELECT id, state FROM backups WHERE database_name = ? ORDER BY id DESC LIMIT 1",
        ("testdb",),
    ).fetchall()
    assert rows, "no backup row was recorded"
    backup_id = str(rows[0]["id"])
    assert rows[0]["state"] == "failed"
    history = catalog.get_backup_history(backup_id=backup_id)
    kinds = [e.event_type.value for e in history]
    assert "download_started" in kinds
    assert "download_failed" in kinds


def test_missing_content_disposition(instance, tmp_path, monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        content=BACKUP_ZIP_CONTENT,
    )

    backup = instance.databases.backup("testdb")
    assert backup.filename.endswith(".zip")
    assert "/" not in backup.filename


def test_unsafe_filename(instance, tmp_path, monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        content=BACKUP_ZIP_CONTENT,
        headers={"content-disposition": 'attachment; filename="../../etc/passwd"'},
    )

    backup = instance.databases.backup("testdb")
    assert "/" not in backup.filename
    assert "\\" not in backup.filename
    assert backup.filename.endswith(".zip")
    assert Path(backup.path).is_file()


def test_server_error(instance, tmp_path, monkeypatch, httpx_mock: HTTPXMock):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        status_code=500,
        content=b"server error",
    )

    with pytest.raises(Exception):
        instance.databases.backup("testdb")
