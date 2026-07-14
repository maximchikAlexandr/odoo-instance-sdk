from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupNotAvailableError, BackupNotFoundError
from odoo_instance_sdk.models import Backup, BackupFormat, BackupValidationStatus
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _create_backup_file(tmp_path: Path, name: str = "backup.zip") -> Path:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    return f


def _u(label: str) -> str:
    # Stable UUID per label so test assertions can match.
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, label))


def _make_backup(
    backup_id: str,
    *,
    source_base_url: str = "http://localhost:8069",
    database_name: str = "db",
    filename: str = "b.zip",
    size_bytes: int = 100,
    sha256: str = "",
    path: Path | None = None,
) -> Backup:
    return Backup(
        id=uuid.UUID(backup_id),
        source_base_url=source_base_url,
        database_name=database_name,
        format=BackupFormat.ZIP,
        filestore_requested=True,
        path=str(path) if path is not None else "",
        filename=filename,
        size_bytes=size_bytes,
        sha256=sha256,
        downloaded_at=datetime.now(UTC),
    )


def test_schema_creation(tmp_path):
    db = tmp_path / "test.db"
    catalog = BackupCatalog(db_path=db)
    tables = catalog._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {r[0] for r in tables}
    assert "backups" in table_names
    assert "backup_events" in table_names
    catalog.close()


def test_start_download_records_event(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "backup.zip")
    bid = _u("start")
    catalog.start_download(
        bid,
        "http://localhost:8069",
        "testdb",
        "zip",
        True,
        path,
    )
    row = catalog._conn.execute("SELECT * FROM backups WHERE id=?", (bid,)).fetchone()
    assert row["state"] == "downloading"
    assert row["source_base_url"] == "http://localhost:8069"
    events = catalog._conn.execute(
        "SELECT event_type, sequence FROM backup_events WHERE backup_id=? ORDER BY sequence",
        (bid,),
    ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "download_started"
    assert events[0]["sequence"] == 1
    catalog.close()


def test_success_download(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "backup.zip")
    bid = _u("success")
    catalog.start_download(bid, "http://localhost:8069", "testdb", "zip", True, path)
    catalog.success_download(bid, "final.zip", 1024, "")
    row = catalog._conn.execute("SELECT * FROM backups WHERE id=?", (bid,)).fetchone()
    assert row["state"] == "available"
    assert row["size_bytes"] == 1024
    assert row["filename"] == "final.zip"
    events = catalog._conn.execute(
        "SELECT event_type FROM backup_events WHERE backup_id=? ORDER BY sequence",
        (bid,),
    ).fetchall()
    kinds = [e["event_type"] for e in events]
    assert kinds == ["download_started", "download_succeeded"]
    catalog.close()


def test_fail_download(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "backup.zip")
    bid = _u("fail")
    catalog.start_download(bid, "http://localhost:8069", "testdb", "zip", True, path)
    catalog.fail_download(bid, "OSError", "connection lost")
    row = catalog._conn.execute("SELECT * FROM backups WHERE id=?", (bid,)).fetchone()
    assert row["state"] == "failed"
    assert row["error_type"] == "OSError"
    events = catalog._conn.execute(
        "SELECT event_type FROM backup_events WHERE backup_id=? ORDER BY sequence",
        (bid,),
    ).fetchall()
    kinds = [e["event_type"] for e in events]
    assert "download_failed" in kinds
    catalog.close()


def test_state_transitions(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "backup.zip")
    bid = _u("state")
    catalog.start_download(bid, "http://localhost:8069", "testdb", "zip", True, path)
    catalog.success_download(bid, "b.zip", 2048, "")
    catalog.record_deletion(bid)
    row = catalog._conn.execute("SELECT * FROM backups WHERE id=?", (bid,)).fetchone()
    assert row["state"] == "deleted"
    catalog.close()


def test_list_backups_empty(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    assert catalog.list_backups() == []
    catalog.close()


def test_list_backups_with_filters(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    for i, (url, db) in enumerate(
        [("http://a:8069", "db1"), ("http://a:8069", "db2"), ("http://b:8069", "db1")],
    ):
        path = _create_backup_file(tmp_path, f"b{i}.zip")
        bid = _u(f"list-{i}")
        catalog.start_download(bid, url, db, "zip", True, path)
        catalog.success_download(bid, f"b{i}.zip", 100, "")
    assert len(catalog.list_backups(source_base_url="http://a:8069")) == 2
    assert len(catalog.list_backups(source_base_url="http://a:8069", database_name="db1")) == 1
    catalog.close()


def test_list_backups_filters_state_available(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path1 = _create_backup_file(tmp_path, "ok.zip")
    bid_ok = _u("filter-ok")
    catalog.start_download(bid_ok, "http://localhost:8069", "db", "zip", True, path1)
    catalog.success_download(bid_ok, "ok.zip", 100, "")
    path2 = _create_backup_file(tmp_path, "no.zip")
    bid_no = _u("filter-no")
    catalog.start_download(bid_no, "http://localhost:8069", "db", "zip", True, path2)
    catalog.fail_download(bid_no, "OSError", "boom")
    listed = catalog.list_backups(source_base_url="http://localhost:8069", database_name="db")
    assert [b.id for b in listed] == [uuid.UUID(bid_ok)]
    catalog.close()


def test_list_backups_skips_missing_files(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "vanish.zip")
    bid = _u("vanish")
    catalog.start_download(bid, "http://localhost:8069", "db", "zip", True, path)
    catalog.success_download(bid, "vanish.zip", 100, "")
    path.unlink()
    assert catalog.list_backups() == []
    catalog.close()


def test_latest_backup(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    p1 = _create_backup_file(tmp_path, "old.zip")
    bid1 = _u("latest-old")
    catalog.start_download(bid1, "http://localhost:8069", "mydb", "zip", True, p1)
    catalog.success_download(bid1, "old.zip", 100, "")
    p2 = _create_backup_file(tmp_path, "new.zip")
    bid2 = _u("latest-new")
    catalog.start_download(bid2, "http://localhost:8069", "mydb", "zip", True, p2)
    catalog.success_download(bid2, "new.zip", 200, "")
    # Force the second download to be later
    catalog._conn.execute(
        "UPDATE backups SET downloaded_at = datetime('now', '+10 seconds') WHERE id = ?",
        (bid2,),
    )
    catalog._conn.commit()
    latest = catalog.latest_backup("http://localhost:8069", "mydb")
    assert latest is not None
    assert latest.id == uuid.UUID(bid2)
    catalog.close()


def test_full_audit_retention(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("audit")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, path)
    catalog.fail_download(bid, "OSError", "boom")
    catalog.record_validation(bid, BackupValidationStatus.INVALID, message="bad")
    events = catalog.get_backup_history(backup_id=bid)
    kinds = [e.event_type.value for e in events]
    assert "download_failed" in kinds
    assert "validation_failed" in kinds
    catalog.close()


def test_validation_succeeded_and_failed_events(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("validate")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")
    catalog.record_validation(
        bid, BackupValidationStatus.VALID, validator="pg_restore", exit_code=0
    )
    events = catalog.get_backup_history(backup_id=bid)
    kinds = [e.event_type.value for e in events]
    assert "validation_succeeded" in kinds
    catalog.close()


def test_ordering(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    bids: list[str] = []
    for i, name in enumerate(["a.zip", "b.zip"]):
        p = _create_backup_file(tmp_path, name)
        bid = _u(f"order-{i}")
        bids.append(bid)
        catalog.start_download(bid, "http://localhost:8069", "db", "zip", True, p)
        catalog.success_download(bid, name, 100, "")
        # Stagger timestamps to keep ordering deterministic
        catalog._conn.execute(
            "UPDATE backups SET downloaded_at = datetime('now', ?) WHERE id = ?",
            (f"+{i} seconds", bid),
        )
    catalog._conn.commit()
    backups = catalog.list_backups()
    assert backups[0].id == uuid.UUID(bids[1])
    assert backups[1].id == uuid.UUID(bids[0])
    catalog.close()


def test_verify_identity_raises(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("verify")
    catalog.start_download(bid, "http://localhost:8069", "db", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")
    catalog.verify_identity(_make_backup(bid, path=path))
    with pytest.raises(BackupNotFoundError):
        catalog.verify_identity(_make_backup(_u("missing"), path=path))
    catalog.close()


def test_verify_identity_rejects_wrong_state(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("verify-state")
    catalog.start_download(bid, "http://localhost:8069", "db", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")
    catalog.record_deletion(bid)
    with pytest.raises(BackupNotAvailableError):
        catalog.verify_identity(_make_backup(bid, path=path))
    catalog.close()


def test_verify_identity_rejects_path_mismatch(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("verify-path")
    catalog.start_download(bid, "http://localhost:8069", "db", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")
    wrong_path = tmp_path / "other.zip"
    wrong_path.write_bytes(b"x")
    with pytest.raises(BackupNotAvailableError, match="path"):
        catalog.verify_identity(_make_backup(bid, path=wrong_path))
    catalog.close()
