from __future__ import annotations

import sqlite3
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


def test_v0_empty_catalog_migration(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 0")
    conn.close()

    catalog = BackupCatalog(db_path=db)
    tables = {
        r[0]
        for r in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "restores" in tables
    assert "database_events" in tables

    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 2

    # Existing backups table still works
    path = _create_backup_file(tmp_path, "migrated.zip")
    bid = _u("migration")
    catalog.start_download(bid, "http://localhost:8069", "db", "zip", True, path)
    row = catalog._conn.execute("SELECT * FROM backups WHERE id=?", (bid,)).fetchone()
    assert row["state"] == "downloading"
    catalog.close()


def test_schema_creation_v0_migration_with_existing_data(tmp_path):
    """v0 → v2 migration MUST preserve existing backups and events."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 0")
    conn.executescript("""
        CREATE TABLE backups (
            id TEXT PRIMARY KEY,
            source_base_url TEXT NOT NULL,
            database_name TEXT NOT NULL,
            format TEXT NOT NULL,
            filestore_requested INTEGER NOT NULL,
            path TEXT,
            filename TEXT,
            size_bytes INTEGER,
            sha256 TEXT,
            state TEXT NOT NULL,
            started_at TEXT NOT NULL,
            downloaded_at TEXT,
            failed_at TEXT,
            deleted_at TEXT,
            error_type TEXT,
            error_message TEXT
        );
        CREATE TABLE backup_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            path TEXT,
            validator TEXT,
            exit_code INTEGER,
            message TEXT
        );
    """)
    pre_backup_id = _u("pre-existing")
    conn.execute(
        "INSERT INTO backups (id, source_base_url, database_name, format, "
        "filestore_requested, state, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pre_backup_id, "http://old:8069", "olddb", "zip", 1, "available", "2020-01-01"),
    )
    conn.execute(
        "INSERT INTO backup_events (backup_id, event_type, occurred_at) VALUES (?, ?, ?)",
        (pre_backup_id, "download_started", "2020-01-01"),
    )
    conn.commit()
    conn.close()

    catalog = BackupCatalog(db_path=db)

    backup_row = catalog._conn.execute(
        "SELECT * FROM backups WHERE id=?", (pre_backup_id,)
    ).fetchone()
    assert backup_row is not None
    assert backup_row["database_name"] == "olddb"
    assert backup_row["state"] == "available"

    event_row = catalog._conn.execute(
        "SELECT * FROM backup_events WHERE backup_id=? ORDER BY sequence",
        (pre_backup_id,),
    ).fetchone()
    assert event_row is not None
    assert event_row["event_type"] == "download_started"

    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 2

    catalog.close()


def test_schema_creation_v2_reopen(tmp_path):
    db = tmp_path / "test.db"
    catalog = BackupCatalog(db_path=db)
    version1 = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version1 == 2
    catalog.close()

    catalog2 = BackupCatalog(db_path=db)
    version2 = catalog2._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version2 == 2
    tables = {
        r[0]
        for r in catalog2._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "restores" in tables
    assert "database_events" in tables
    catalog2.close()


def test_normalize_db_host():
    from odoo_instance_sdk.storage.backup_catalog import normalize_db_host

    assert normalize_db_host(None) == "socket"
    assert normalize_db_host("localhost") == "localhost"
    assert normalize_db_host("192.168.1.1") == "192.168.1.1"


def test_record_restore_inserts_rows(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("record-restore")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")

    catalog.record_restore("localhost", 5432, "mydb", bid)

    restores = catalog._conn.execute(
        "SELECT * FROM restores WHERE db_host=? AND db_port=? AND database_name=?",
        ("localhost", 5432, "mydb"),
    ).fetchall()
    assert len(restores) == 1
    assert restores[0]["backup_id"] == bid

    events = catalog._conn.execute(
        "SELECT event_type, backup_id FROM database_events WHERE db_host=? AND db_port=? AND database_name=? ORDER BY sequence",
        ("localhost", 5432, "mydb"),
    ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "restored"
    assert events[0]["backup_id"] == bid

    latest = catalog.latest_restore("localhost", 5432, "mydb")
    assert latest is not None
    assert latest.id == uuid.UUID(bid)
    catalog.close()


def test_record_restore_normalizes_socket(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("socket-restore")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")

    catalog.record_restore(None, 5432, "mydb", bid)

    rows = catalog._conn.execute(
        "SELECT db_host FROM restores WHERE database_name=?", ("mydb",)
    ).fetchall()
    assert rows[0]["db_host"] == "socket"

    latest = catalog.latest_restore(None, 5432, "mydb")
    assert latest is not None
    assert latest.id == uuid.UUID(bid)
    catalog.close()


def test_duplicate_restore_inserts_two_rows(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("dup-restore")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")

    catalog.record_restore("localhost", 5432, "mydb", bid)
    catalog.record_restore("localhost", 5432, "mydb", bid)

    rows = catalog._conn.execute(
        "SELECT COUNT(*) FROM restores WHERE db_host=? AND db_port=? AND database_name=?",
        ("localhost", 5432, "mydb"),
    ).fetchone()
    assert rows[0] == 2

    latest = catalog.latest_restore("localhost", 5432, "mydb")
    assert latest is not None
    assert latest.id == uuid.UUID(bid)
    catalog.close()


def test_latest_restore_backup_deleted_returns_none(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("deleted-restore")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")
    catalog.record_restore("localhost", 5432, "mydb", bid)
    catalog.record_deletion(bid)

    latest = catalog.latest_restore("localhost", 5432, "mydb")
    assert latest is None
    catalog.close()


def test_latest_restore_file_missing_returns_none(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "vanish.zip")
    bid = _u("missing-file")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, path)
    catalog.success_download(bid, "vanish.zip", 100, "")
    catalog.record_restore("localhost", 5432, "mydb", bid)
    path.unlink()

    latest = catalog.latest_restore("localhost", 5432, "mydb")
    assert latest is None
    catalog.close()


def test_latest_restore_no_fallback_to_earlier(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    p1 = _create_backup_file(tmp_path, "b1.zip")
    p2 = _create_backup_file(tmp_path, "b2.zip")
    bid1 = _u("no-fallback-1")
    bid2 = _u("no-fallback-2")
    catalog.start_download(bid1, "http://localhost:8069", "mydb", "zip", True, p1)
    catalog.start_download(bid2, "http://localhost:8069", "mydb", "zip", True, p2)
    catalog.success_download(bid1, "b1.zip", 100, "")
    catalog.success_download(bid2, "b2.zip", 200, "")

    catalog.record_restore("localhost", 5432, "mydb", bid1)
    catalog._conn.execute(
        "UPDATE restores SET restored_at = datetime('now', '-1 day') WHERE backup_id = ?",
        (bid1,),
    )
    catalog.record_restore("localhost", 5432, "mydb", bid2)
    catalog.record_deletion(bid2)

    latest = catalog.latest_restore("localhost", 5432, "mydb")
    assert latest is None
    catalog.close()


def test_record_database_dropped_idempotent(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    catalog.record_database_dropped("localhost", 5432, "mydb")
    catalog.record_database_dropped("localhost", 5432, "mydb")

    events = catalog._conn.execute(
        "SELECT event_type FROM database_events WHERE db_host=? AND db_port=? AND database_name=? ORDER BY sequence",
        ("localhost", 5432, "mydb"),
    ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "dropped"
    catalog.close()


def test_record_database_dropped_normalizes_socket(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    catalog.record_database_dropped(None, 5432, "mydb")

    events = catalog._conn.execute(
        "SELECT db_host, event_type FROM database_events WHERE database_name=? ORDER BY sequence",
        ("mydb",),
    ).fetchall()
    assert events[0]["db_host"] == "socket"
    assert events[0]["event_type"] == "dropped"
    catalog.close()


def test_restore_after_dropped_resets(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    path = _create_backup_file(tmp_path, "b.zip")
    bid = _u("reset-test")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, path)
    catalog.success_download(bid, "b.zip", 100, "")

    catalog.record_database_dropped("localhost", 5432, "mydb")
    catalog.record_restore("localhost", 5432, "mydb", bid)

    latest = catalog.latest_restore("localhost", 5432, "mydb")
    assert latest is not None
    assert latest.id == uuid.UUID(bid)
    catalog.close()


def test_distinct_restored_database_names(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    p1 = _create_backup_file(tmp_path, "db1.zip")
    p2 = _create_backup_file(tmp_path, "db2.zip")
    bid1 = _u("distinct-1")
    bid2 = _u("distinct-2")
    catalog.start_download(bid1, "http://localhost:8069", "db1", "zip", True, p1)
    catalog.start_download(bid2, "http://localhost:8069", "db2", "zip", True, p2)
    catalog.success_download(bid1, "db1.zip", 100, "")
    catalog.success_download(bid2, "db2.zip", 200, "")

    catalog.record_restore("localhost", 5432, "db1", bid1)
    catalog.record_restore("localhost", 5432, "db1", bid1)
    catalog.record_restore("localhost", 5432, "db2", bid2)

    names = catalog.distinct_restored_database_names("localhost", 5432)
    assert sorted(names) == ["db1", "db2"]
    catalog.close()


def test_distinct_restored_database_names_normalizes_socket(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    bid = _u("distinct-socket")
    p = _create_backup_file(tmp_path, "s.zip")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, p)
    catalog.success_download(bid, "s.zip", 100, "")

    catalog.record_restore(None, 5432, "mydb", bid)

    names = catalog.distinct_restored_database_names(None, 5432)
    assert names == ("mydb",)
    catalog.close()


def test_latest_restore_empty_returns_none(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    latest = catalog.latest_restore("localhost", 5432, "nonexistent")
    assert latest is None
    catalog.close()


def test_has_tracked_database_true(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    p = _create_backup_file(tmp_path, "b.zip")
    bid = _u("has-tracked")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, p)
    catalog.success_download(bid, "b.zip", 100, "")
    catalog.record_restore("localhost", 5432, "mydb", bid)
    assert catalog.has_tracked_database("localhost", 5432, "mydb") is True
    catalog.close()


def test_has_tracked_database_false(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    assert catalog.has_tracked_database("localhost", 5432, "ghost") is False
    catalog.close()


def test_has_tracked_database_normalizes_socket(tmp_path):
    catalog = BackupCatalog(db_path=tmp_path / "test.db")
    p = _create_backup_file(tmp_path, "s.zip")
    bid = _u("socket-has")
    catalog.start_download(bid, "http://localhost:8069", "mydb", "zip", True, p)
    catalog.success_download(bid, "s.zip", 100, "")
    catalog.record_restore(None, 5432, "mydb", bid)
    assert catalog.has_tracked_database(None, 5432, "mydb") is True
    catalog.close()
