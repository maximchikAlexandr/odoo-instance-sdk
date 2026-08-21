from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    import pytest


def _make_legacy_v2(db_path: Path) -> str:
    bid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA user_version = 2")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backups (
            id TEXT PRIMARY KEY, source_base_url TEXT NOT NULL,
            database_name TEXT NOT NULL, format TEXT NOT NULL,
            filestore_requested INTEGER NOT NULL, path TEXT,
            filename TEXT, size_bytes INTEGER, sha256 TEXT,
            state TEXT NOT NULL, started_at TEXT NOT NULL,
            downloaded_at TEXT, failed_at TEXT, deleted_at TEXT,
            error_type TEXT, error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS backup_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_id TEXT NOT NULL, event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL, path TEXT, validator TEXT,
            exit_code INTEGER, message TEXT
        );
        CREATE TABLE IF NOT EXISTS restores (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL, db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL, backup_id TEXT NOT NULL,
            restored_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS database_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL, db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL, event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL, backup_id TEXT
        );
    """)
    conn.execute(
        "INSERT INTO backups (id, source_base_url, database_name, format, "
        "filestore_requested, state, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (bid, "http://localhost:8069", "mydb", "zip", 1, "available", "2020-01-01"),
    )
    conn.commit()
    conn.close()
    return bid


def test_fresh_install_creates_v6_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    durable = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.storage.backup_catalog.get_legacy_catalog_path",
        lambda: tmp_path / "nonexistent.sqlite3",
    )
    catalog = BackupCatalog(db_path=durable)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 7
    tables = {
        r[0]
        for r in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "environments" in tables
    assert "environment_events" in tables
    assert not catalog._migrated_legacy
    catalog.close()


def test_cache_to_data_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    durable = tmp_path / "data" / "catalog.sqlite3"
    legacy = tmp_path / "cache" / "backups.sqlite3"
    legacy.parent.mkdir(parents=True)
    bid = _make_legacy_v2(legacy)

    monkeypatch.setattr(
        "odoo_instance_sdk.storage.backup_catalog.get_legacy_catalog_path", lambda: legacy
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_catalog_path", lambda: durable)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.locks.catalog_migration_lock_path",
        lambda: tmp_path / "migration.lock",
    )

    catalog = BackupCatalog(db_path=durable)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 7
    row = catalog._conn.execute("SELECT * FROM backups WHERE id = ?", (bid,)).fetchone()
    assert row is not None
    assert row["database_name"] == "mydb"
    assert legacy.exists()
    assert catalog._migrated_legacy
    catalog.close()


def test_both_exist_durable_authoritative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    durable = tmp_path / "data" / "catalog.sqlite3"
    legacy = tmp_path / "cache" / "backups.sqlite3"
    legacy.parent.mkdir(parents=True)
    _make_legacy_v2(legacy)
    durable.parent.mkdir(parents=True)
    durable_bid = str(uuid.uuid4())
    conn = sqlite3.connect(str(durable))
    conn.execute("PRAGMA user_version = 3")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backups (
            id TEXT PRIMARY KEY, source_base_url TEXT NOT NULL,
            database_name TEXT NOT NULL, format TEXT NOT NULL,
            filestore_requested INTEGER NOT NULL, path TEXT,
            filename TEXT, size_bytes INTEGER, sha256 TEXT,
            state TEXT NOT NULL, started_at TEXT NOT NULL,
            downloaded_at TEXT, failed_at TEXT, deleted_at TEXT,
            error_type TEXT, error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS environments (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, repository_root TEXT NOT NULL,
            git_common_dir TEXT NOT NULL, branch TEXT NOT NULL, base_ref TEXT NOT NULL,
            worktree_path TEXT NOT NULL, generated_config_path TEXT NOT NULL,
            python_environment_path TEXT NOT NULL, python_environment_owned INTEGER NOT NULL,
            dependency_lock_path TEXT NOT NULL, http_interface TEXT NOT NULL,
            http_port INTEGER NOT NULL, db_mode TEXT NOT NULL,
            source_db_name TEXT, target_db_name TEXT, backup_id TEXT,
            runtime_json TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, last_used_at TEXT, removed_at TEXT,
            last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS environment_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            environment_id TEXT NOT NULL, operation TEXT NOT NULL,
            outcome TEXT NOT NULL, occurred_at TEXT NOT NULL, message TEXT
        );
    """)
    conn.execute(
        "INSERT INTO backups (id, source_base_url, database_name, format, "
        "filestore_requested, state, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (durable_bid, "http://durable:8069", "durable_db", "zip", 1, "available", "2021-01-01"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "odoo_instance_sdk.storage.backup_catalog.get_legacy_catalog_path", lambda: legacy
    )
    monkeypatch.setattr("odoo_instance_sdk.internal.paths.get_catalog_path", lambda: durable)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.locks.catalog_migration_lock_path",
        lambda: tmp_path / "migration.lock",
    )

    catalog = BackupCatalog(db_path=durable)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 7
    row = catalog._conn.execute("SELECT * FROM backups WHERE id = ?", (durable_bid,)).fetchone()
    assert row is not None
    assert row["database_name"] == "durable_db"
    assert not catalog._migrated_legacy
    legacy_path = catalog.legacy_catalog_path()
    assert legacy_path == legacy
    catalog.close()


def test_environment_methods_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    durable = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.storage.backup_catalog.get_legacy_catalog_path",
        lambda: tmp_path / "nonexistent.sqlite3",
    )
    catalog = BackupCatalog(db_path=durable)
    env_id = str(uuid.uuid4())
    catalog.create_environment(
        {
            "id": env_id,
            "name": "test",
            "repository_root": "/repo",
            "git_common_dir": "/repo/.git",
            "branch": "main",
            "base_ref": "HEAD",
            "worktree_path": "/wt",
            "generated_config_path": "/wt/odoo.conf",
            "python_environment_path": "/venv",
            "python_environment_owned": False,
            "dependency_lock_path": "/lock",
            "http_interface": "127.0.0.1",
            "http_port": 8069,
            "db_mode": "shared",
            "source_db_name": "mydb",
            "target_db_name": None,
            "backup_id": None,
            "runtime_json": "{}",
            "state": "creating",
            "created_at": "2026-01-01T00:00:00",
            "last_used_at": None,
            "removed_at": None,
            "last_error": None,
        }
    )
    row = catalog.get_environment(env_id)
    assert row is not None
    assert row["name"] == "test"
    catalog.add_environment_event(env_id, "checkout", "started", message="begin")
    catalog.update_environment_state(env_id, "ready")
    assert catalog.active_environment_for("/repo/.git", "main") is not None
    assert catalog.active_environment_for_port(8069) is not None
    envs = catalog.list_environments()
    assert len(envs) == 1
    catalog.close()


def test_v5_copy_journal_migrates_to_typed_pending_stage(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 5")
    conn.executescript("""
        CREATE TABLE backups (
            id TEXT PRIMARY KEY,
            source_base_url TEXT NOT NULL,
            database_name TEXT NOT NULL,
            state TEXT NOT NULL,
            downloaded_at TEXT
        );
        CREATE TABLE environments (id TEXT PRIMARY KEY);
        CREATE TABLE environment_copy_journal (
            environment_id TEXT PRIMARY KEY REFERENCES environments(id),
            target_database TEXT NOT NULL,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            db_user TEXT,
            backup_id TEXT REFERENCES backups(id),
            stage TEXT NOT NULL CHECK (stage IN ('prepared', 'backed_up', 'restored', 'dropped', 'backup_deleted')),
            updated_at TEXT NOT NULL
        );
    """)
    conn.close()

    catalog = BackupCatalog(db_path=db)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    schema = catalog._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='environment_copy_journal'"
    ).fetchone()[0]
    assert version == 7
    assert "restore_pending" in schema
    catalog.close()
