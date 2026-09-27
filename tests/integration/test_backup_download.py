from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pytest_httpx import HTTPXMock

    from odoo_instance_sdk.resources.instance import OdooInstance

from odoo_instance_sdk.exceptions import BackupDownloadError
from odoo_instance_sdk.internal.dbprep import materialize as preparation
from odoo_instance_sdk.internal.paths import get_backups_dir
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import DatabaseRefreshOptions
from odoo_instance_sdk.project import ProjectConfig, RemoteSourceConfig


def _patch_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_kwargs: db_path
    )


def test_successful_download(
    instance: OdooInstance, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
    )
    _patch_catalog(monkeypatch, tmp_path)
    instance._client._catalog = None
    from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        content=BACKUP_ZIP_CONTENT,
        headers={"content-disposition": 'attachment; filename="testdb_backup.zip"'},
    )

    backup = instance.databases.backup("testdb", source_git_branch="main")
    assert backup.database_name == "testdb"
    assert backup.size_bytes == len(BACKUP_ZIP_CONTENT)
    assert backup.filename.endswith(".zip")
    assert backup.source_git_branch == "main"
    assert "/" not in backup.filename
    assert Path(backup.path).is_file()


def test_named_source_cross_origin_redirect_does_not_forward_credentials(
    instance: OdooInstance,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
) -> None:
    project = ProjectConfig(
        repository_root=tmp_path,
        remote_instances=(
            RemoteSourceConfig(
                name="staging",
                base_url="https://staging.example",
                database="testdb",
                git_branch="main",
            ),
        ),
    )
    monkeypatch.setenv("ODCLI_REMOTE_STAGING_MASTER_PASSWORD", "staging-secret")
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
    )
    monkeypatch.setattr(
        preparation,
        "canonical_project_identity",
        lambda _: (tmp_path, tmp_path, repo_key(tmp_path, tmp_path)),
    )
    _patch_catalog(monkeypatch, tmp_path)
    instance._client._catalog = None
    httpx_mock.add_response(
        url="https://staging.example/web/database/backup",
        method="POST",
        status_code=302,
        headers={"location": "https://other.example/web/database/backup"},
    )

    with pytest.raises(BackupDownloadError, match="302"):
        preparation.prepare_download(
            instance._client,
            project,
            options=DatabaseRefreshOptions(remote_name="staging"),
        )

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert str(requests[0].url) == "https://staging.example/web/database/backup"
    assert b"staging-secret" in requests[0].content


def test_backup_round_trips_through_catalog(
    instance: OdooInstance, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
    )
    _patch_catalog(monkeypatch, tmp_path)
    instance._client._catalog = None
    from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        content=BACKUP_ZIP_CONTENT,
        headers={"content-disposition": 'attachment; filename="testdb_backup.zip"'},
    )

    backup = instance.databases.backup("testdb", source_git_branch="main")

    catalog = instance._client.get_catalog()
    backups = catalog.list_backups(source_base_url=instance.config.base_url, database_name="testdb")
    assert len(backups) == 1
    assert backups[0].id == backup.id
    assert backups[0].filename == backup.filename
    assert backups[0].size_bytes == backup.size_bytes
    assert backups[0].path == backup.path
    assert backups[0].source_git_branch == "main"

    history = catalog.get_backup_history(backup_id=str(backup.id))
    kinds = [e.event_type.value for e in history]
    assert "download_started" in kinds
    assert "download_succeeded" in kinds


def test_interrupted_download(
    instance: OdooInstance, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
    )

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
    instance: OdooInstance, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
    )
    _patch_catalog(monkeypatch, tmp_path)
    instance._client._catalog = None

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


def test_missing_content_disposition(
    instance: OdooInstance, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
    )
    from tests.fixtures.odoo_database_server import BACKUP_ZIP_CONTENT

    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        content=BACKUP_ZIP_CONTENT,
    )

    backup = instance.databases.backup("testdb")
    assert backup.filename.endswith(".zip")
    assert "/" not in backup.filename


def test_unsafe_filename(
    instance: OdooInstance, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
    )
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


def test_server_error(
    instance: OdooInstance, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_cache_root", lambda **_kwargs: tmp_path
    )
    httpx_mock.add_response(
        url="http://localhost:8069/web/database/backup",
        method="POST",
        status_code=500,
        content=b"server error",
    )

    with pytest.raises(Exception):
        instance.databases.backup("testdb")
