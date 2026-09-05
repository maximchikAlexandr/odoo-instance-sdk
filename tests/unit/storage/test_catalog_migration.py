from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.storage.backup_catalog import (
    CURRENT_SCHEMA_VERSION,
    BackupCatalog,
)

CATALOG_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
NEXT_CATALOG_SCHEMA_VERSION = CATALOG_SCHEMA_VERSION + 1
# These are the pre-change upgrade states represented by the migration tests;
# keeping the list explicit makes a missing intermediate fixture fail loudly.
MIGRATION_FIXTURE_VERSIONS = (5, 6, 7, 8, 9, 10, 11)


def test_next_catalog_migration_version_and_fixtures_are_sequential() -> None:
    assert CATALOG_SCHEMA_VERSION == 11
    assert NEXT_CATALOG_SCHEMA_VERSION == 12
    contiguous_versions = tuple(range(MIGRATION_FIXTURE_VERSIONS[0], CATALOG_SCHEMA_VERSION + 1))
    assert contiguous_versions == MIGRATION_FIXTURE_VERSIONS


def test_fresh_install_creates_v11_directly(tmp_path: Path) -> None:
    durable = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=durable)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 11
    backup_columns = {r[1] for r in catalog._conn.execute("PRAGMA table_info(backups)").fetchall()}
    assert "source_git_branch" in backup_columns
    tables = {
        r[0]
        for r in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "environments" in tables
    assert "environment_events" in tables
    assert "runtime" in tables
    assert "projects" in tables
    catalog.close()


def test_v9_catalog_migrates_branch_column_and_preserves_mapping_and_events(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    backup_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA user_version = 9")
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
            backup_id TEXT NOT NULL REFERENCES backups(id),
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            path TEXT,
            validator TEXT,
            exit_code INTEGER,
            message TEXT
        );
        CREATE TABLE restores (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            backup_id TEXT NOT NULL REFERENCES backups(id),
            restored_at TEXT NOT NULL
        );
        CREATE TABLE database_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            backup_id TEXT REFERENCES backups(id)
        );
    """)
    conn.execute(
        "INSERT INTO backups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            backup_id,
            "https://example.test",
            "source",
            "zip",
            1,
            str(tmp_path / "gone.zip"),
            "gone.zip",
            7,
            "a" * 64,
            "deleted",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
            None,
            "2026-01-02T00:00:00+00:00",
            None,
            None,
        ),
    )
    conn.execute(
        "INSERT INTO backup_events (backup_id, event_type, occurred_at) VALUES (?, ?, ?)",
        (backup_id, "download_succeeded", "2026-01-01T00:00:01+00:00"),
    )
    conn.execute(
        "INSERT INTO restores (db_host, db_port, database_name, backup_id, restored_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("localhost", 5432, "restored", backup_id, "2026-01-02T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO database_events (db_host, db_port, database_name, event_type, occurred_at, backup_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("localhost", 5432, "restored", "restored", "2026-01-02T00:00:00+00:00", backup_id),
    )
    conn.commit()
    conn.close()

    catalog = BackupCatalog(db_path=db)
    assert catalog._conn.execute("PRAGMA user_version").fetchone()[0] == 11
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM backup_events WHERE backup_id=?", (backup_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM restores WHERE backup_id=?", (backup_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM database_events WHERE backup_id=?", (backup_id,)
        ).fetchone()[0]
        == 1
    )
    catalog.close()

    reopened = BackupCatalog(db_path=db)
    columns = [row[1] for row in reopened._conn.execute("PRAGMA table_info(backups)")]
    assert columns.count("source_git_branch") == 1
    provenance = reopened.latest_restore_provenance("localhost", 5432, "restored")
    assert provenance is not None
    assert provenance.source_git_branch is None
    assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 11
    reopened.close()


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
    assert version == 11
    assert "restore_pending" in schema
    catalog.close()


def test_v8_catalog_upgrades_to_v11_environment_runtime_and_branch_column(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 8")
    conn.executescript("""
        CREATE TABLE backups (
            id TEXT PRIMARY KEY,
            source_base_url TEXT NOT NULL,
            database_name TEXT NOT NULL,
            state TEXT NOT NULL,
            downloaded_at TEXT
        );
        CREATE TABLE environments (id TEXT PRIMARY KEY);
    """)
    conn.close()

    catalog = BackupCatalog(db_path=db)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        r[0]
        for r in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert version == 11
    assert "runtime" in tables
    columns = {row[1] for row in catalog._conn.execute("PRAGMA table_info(backups)")}
    assert "source_git_branch" in columns
    assert len([column for column in columns if column == "source_git_branch"]) == 1
    catalog.close()


def _write_v7_catalog_with_environment(db: Path, env_id: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 7")
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
        CREATE TABLE environments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            repository_root TEXT NOT NULL,
            git_common_dir TEXT NOT NULL,
            branch TEXT NOT NULL,
            base_ref TEXT NOT NULL,
            worktree_path TEXT NOT NULL,
            generated_config_path TEXT NOT NULL,
            python_environment_path TEXT NOT NULL,
            python_environment_owned INTEGER NOT NULL,
            dependency_lock_path TEXT NOT NULL,
            http_interface TEXT NOT NULL,
            http_port INTEGER NOT NULL,
            db_mode TEXT NOT NULL,
            source_db_name TEXT,
            target_db_name TEXT,
            backup_id TEXT,
            runtime_json TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            removed_at TEXT,
            last_error TEXT,
            FOREIGN KEY (backup_id) REFERENCES backups(id)
        );
        CREATE TABLE environment_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            environment_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            outcome TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            message TEXT,
            FOREIGN KEY (environment_id) REFERENCES environments(id)
        );
        CREATE UNIQUE INDEX environments_one_active_branch
            ON environments(git_common_dir, branch) WHERE state <> 'removed';
    """)
    conn.execute(
        """INSERT INTO environments (
            id, name, repository_root, git_common_dir, branch, base_ref,
            worktree_path, generated_config_path, python_environment_path,
            python_environment_owned, dependency_lock_path, http_interface,
            http_port, db_mode, source_db_name, target_db_name, backup_id,
            runtime_json, state, created_at, last_used_at, removed_at, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            env_id,
            "test",
            "/repo",
            "/repo/.git",
            "main",
            "HEAD",
            "/wt",
            "/wt/odoo.conf",
            "/venv",
            0,
            "/lock",
            "127.0.0.1",
            8077,
            "shared",
            "mydb",
            None,
            None,
            "{}",
            "ready",
            "2026-01-01T00:00:00",
            None,
            None,
            None,
        ),
    )
    conn.execute(
        """INSERT INTO environment_events
           (environment_id, operation, outcome, occurred_at, message)
           VALUES (?, 'checkout', 'succeeded', '2026-01-01T00:00:00', NULL)""",
        (env_id,),
    )
    conn.commit()
    conn.close()


def test_v7_catalog_drops_http_port_columns(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    env_id = str(uuid.uuid4())
    _write_v7_catalog_with_environment(db, env_id)

    catalog = BackupCatalog(db_path=db)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    columns = {row[1] for row in catalog._conn.execute("PRAGMA table_info(environments)")}
    indexes = {row[1] for row in catalog._conn.execute("PRAGMA index_list(environments)")}
    row = catalog.get_environment(env_id)
    event = catalog._conn.execute(
        "SELECT environment_id FROM environment_events WHERE environment_id=?",
        (env_id,),
    ).fetchone()

    assert version == 11
    assert "http_port" not in columns
    assert "http_interface" not in columns
    assert "environments_one_active_branch" in indexes
    assert row is not None
    assert row["name"] == "test"
    assert row["branch"] == "main"
    assert event is not None
    catalog.close()


def test_v8_catalog_keeps_one_active_environment_per_branch(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    env_id = str(uuid.uuid4())
    _write_v7_catalog_with_environment(db, env_id)
    catalog = BackupCatalog(db_path=db)
    with pytest.raises(BackupCatalogError):
        catalog.create_environment(
            {
                "id": str(uuid.uuid4()),
                "name": "other",
                "repository_root": "/repo",
                "git_common_dir": "/repo/.git",
                "branch": "main",
                "base_ref": "HEAD",
                "worktree_path": "/wt2",
                "generated_config_path": "/wt2/odoo.conf",
                "python_environment_path": "/venv",
                "python_environment_owned": False,
                "dependency_lock_path": "/lock",
                "db_mode": "shared",
                "source_db_name": "mydb",
                "target_db_name": None,
                "backup_id": None,
                "runtime_json": "{}",
                "state": "ready",
                "created_at": "2026-01-02T00:00:00",
                "last_used_at": None,
                "removed_at": None,
                "last_error": None,
            }
        )
    catalog.close()
