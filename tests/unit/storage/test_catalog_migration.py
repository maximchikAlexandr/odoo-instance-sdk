from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def test_fresh_install_creates_v7_directly(tmp_path: Path) -> None:
    durable = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=durable)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 8
    tables = {
        r[0]
        for r in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "environments" in tables
    assert "environment_events" in tables
    catalog.close()


def test_environment_methods_exist(tmp_path: Path) -> None:
    durable = tmp_path / "catalog.sqlite3"
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
    assert version == 8
    assert "restore_pending" in schema
    catalog.close()
