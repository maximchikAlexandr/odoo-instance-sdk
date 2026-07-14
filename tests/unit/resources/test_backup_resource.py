from __future__ import annotations

import uuid

import pytest

from odoo_instance_sdk.exceptions import BackupNotFoundError
from odoo_instance_sdk.models import BackupDeletionResult
from odoo_instance_sdk.resources.backup import BackupResource
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.fixtures import make_backup


@pytest.fixture
def sample_backup_entry(client, tmp_path, monkeypatch):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    db_path = tmp_path / "backups.sqlite3"
    backup_file = tmp_path / "real_backup.zip"
    backup_file.write_bytes(b"x")
    bid = str(uuid.uuid4())
    catalog = BackupCatalog(db_path=db_path)
    catalog.start_download(
        bid,
        "http://localhost:8069",
        "mydb",
        "zip",
        True,
        backup_file,
    )
    catalog.success_download(bid, "real_backup.zip", 1024, "abc123")
    catalog.close()
    yield {"path": backup_file, "db_path": db_path, "client": client, "tmp": tmp_path, "id": bid}


def test_list_empty(client, tmp_path, monkeypatch):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    res = BackupResource(_client=client)
    backups = res.list()
    assert backups == ()


def test_list_with_entries(client, sample_backup_entry):
    res = BackupResource(_client=client)
    backups = res.list(source_base_url="http://localhost:8069")
    assert len(backups) == 1
    assert backups[0].database_name == "mydb"
    assert backups[0].id == uuid.UUID(sample_backup_entry["id"])


def test_latest_backup(client, sample_backup_entry):
    res = BackupResource(_client=client)
    latest = res.latest("http://localhost:8069", "mydb")
    assert latest is not None
    assert latest.database_name == "mydb"
    assert latest.id == uuid.UUID(sample_backup_entry["id"])


def test_latest_backup_none(client, tmp_path, monkeypatch):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    res = BackupResource(_client=client)
    assert res.latest("http://localhost:8069", "nonexistent") is None


def test_history_with_started_event(client, sample_backup_entry):
    res = BackupResource(_client=client)
    events = res.history(backup_id=sample_backup_entry["id"])
    assert len(events) >= 1
    assert any(e.event_type.value == "download_started" for e in events)


def test_history_empty(client, sample_backup_entry):
    res = BackupResource(_client=client)
    events = res.history()
    sample_events = [
        e for e in events if e.event_type.value not in ("download_started", "download_succeeded")
    ]
    assert len(sample_events) == 0


def test_delete_idempotent(client, sample_backup_entry):
    res = BackupResource(_client=client)
    backup = make_backup(
        id=uuid.UUID(sample_backup_entry["id"]),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(sample_backup_entry["path"]),
        filename=sample_backup_entry["path"].name,
        size_bytes=1024,
        sha256="abc123",
    )
    result = res.delete(backup)
    assert isinstance(result, BackupDeletionResult)
    assert result.already_deleted is False
    assert result.file_existed is True
    assert result.deleted_at is not None

    second = res.delete(backup)
    assert second.already_deleted is True
    assert second.deleted_at is not None


def test_list_skips_missing_file(client, sample_backup_entry):
    sample_backup_entry["path"].unlink()
    res = BackupResource(_client=client)
    backups = res.list(source_base_url="http://localhost:8069")
    assert backups == ()


def test_full_audit_visibility(client, sample_backup_entry):
    res = BackupResource(_client=client)
    backup = make_backup(
        id=uuid.UUID(sample_backup_entry["id"]),
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(sample_backup_entry["path"]),
        filename=sample_backup_entry["path"].name,
        size_bytes=1024,
        sha256="abc123",
    )
    res.delete(backup)
    events = res.history(backup_id=sample_backup_entry["id"])
    kinds = [e.event_type.value for e in events]
    assert "download_started" in kinds
    assert "download_succeeded" in kinds
    assert "deleted" in kinds


def test_cross_process_rehydration(client, tmp_path, monkeypatch):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    backup_file = tmp_path / "across.zip"
    backup_file.write_bytes(b"x")
    bid = str(uuid.uuid4())

    db_path = tmp_path / "backups.sqlite3"
    catalog1 = BackupCatalog(db_path=db_path)
    catalog1.start_download(
        bid,
        "http://localhost:8069",
        "mydb",
        "zip",
        True,
        backup_file,
    )
    catalog1.success_download(bid, "across.zip", 2048, "def456")
    catalog1.close()

    res = BackupResource(_client=client)
    backups = res.list(source_base_url="http://localhost:8069", database_name="mydb")
    assert len(backups) == 1
    assert backups[0].id == uuid.UUID(bid)
    assert backups[0].size_bytes == 2048


def test_backup_resource_repr(client, tmp_path, monkeypatch):
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_cache_root", lambda: tmp_path)
    res = BackupResource(_client=client)
    r = repr(res)
    assert "BackupResource" in r


def test_delete_unknown_id_raises(client, sample_backup_entry):
    res = BackupResource(_client=client)
    backup = make_backup(
        source_base_url="http://localhost:8069",
        database_name="mydb",
        path=str(sample_backup_entry["path"]),
        filename=sample_backup_entry["path"].name,
    )
    with pytest.raises(BackupNotFoundError):
        res.delete(backup)
