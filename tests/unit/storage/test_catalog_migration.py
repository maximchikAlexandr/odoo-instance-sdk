from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.applied_settings import (
    LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON,
    encode_applied_settings,
)
from odoo_instance_sdk.storage.backup_catalog import (
    CURRENT_SCHEMA_VERSION,
    BackupCatalog,
)
from tests.unit.monitor_support import make_env

CATALOG_SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
NEXT_CATALOG_SCHEMA_VERSION = CATALOG_SCHEMA_VERSION + 1
# These are the pre-change upgrade states represented by the migration tests;
# keeping the list explicit makes a missing intermediate fixture fail loudly.
MIGRATION_FIXTURE_VERSIONS = (5, 6, 7, 8, 9, 10, 11, 12, 13, 14)


def test_next_catalog_migration_version_and_fixtures_are_sequential() -> None:
    assert CATALOG_SCHEMA_VERSION == 15
    assert NEXT_CATALOG_SCHEMA_VERSION == 16
    contiguous_versions = tuple(range(MIGRATION_FIXTURE_VERSIONS[0], CATALOG_SCHEMA_VERSION))
    assert contiguous_versions == MIGRATION_FIXTURE_VERSIONS


def test_fresh_install_creates_v15_directly(tmp_path: Path) -> None:
    durable = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=durable)
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == CATALOG_SCHEMA_VERSION
    backup_columns = {r[1] for r in catalog._conn.execute("PRAGMA table_info(backups)").fetchall()}
    assert "source_git_branch" in backup_columns
    environment_columns = {
        r[1] for r in catalog._conn.execute("PRAGMA table_info(environments)").fetchall()
    }
    assert "applied_settings_json" in environment_columns
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
    for child_table in ("environment_events", "environment_copy_journal"):
        environment_foreign_keys = {
            (foreign_key[3], foreign_key[2], foreign_key[4])
            for foreign_key in catalog._conn.execute(f"PRAGMA foreign_key_list({child_table})")
        }
        assert ("environment_id", "environments", "id") in environment_foreign_keys
    indexes = {r[1] for r in catalog._conn.execute("PRAGMA index_list(backups)").fetchall()}
    assert "backups_point_order_idx" in indexes
    catalog.close()


def test_v15_applied_settings_migration_is_additive_and_unknown(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    env_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 14")
    conn.executescript(
        """
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
            db_mode TEXT NOT NULL,
            source_db_name TEXT,
            target_db_name TEXT,
            backup_id TEXT,
            runtime_json TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            removed_at TEXT,
            last_error TEXT
        );
        CREATE INDEX environments_active_idx ON environments(git_common_dir, branch, state);
        CREATE TABLE environment_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            environment_id TEXT NOT NULL REFERENCES environments(id),
            operation TEXT NOT NULL,
            outcome TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            message TEXT
        );
        INSERT INTO environments VALUES (
            '"""
        + env_id
        + """', 'legacy', '/repo', '/repo/.git', 'main', 'HEAD',
            '/wt', '/wt/odoo.conf', '/venv', 0, '/lock', 'shared', NULL, NULL, NULL,
            '{}', 'ready', '2026-01-01T00:00:00', NULL, NULL, NULL
        );
        INSERT INTO environment_events(environment_id, operation, outcome, occurred_at)
        VALUES ('"""
        + env_id
        + """', 'checkout', 'succeeded', '2026-01-01T00:00:00');
        """
    )
    conn.commit()
    conn.close()

    catalog = BackupCatalog(db_path=db)
    columns = {row[1] for row in catalog._conn.execute("PRAGMA table_info(environments)")}
    row = catalog.get_environment(env_id)
    assert catalog._conn.execute("PRAGMA user_version").fetchone()[0] == 15
    assert "applied_settings_json" in columns
    assert row is not None
    assert row["applied_settings_json"] == LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON
    assert catalog._conn.execute("SELECT COUNT(*) FROM environment_events").fetchone()[0] == 1
    assert (
        catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("environments_active_idx",),
        ).fetchone()
        is not None
    )
    foreign_keys = catalog._conn.execute("PRAGMA foreign_key_list(environment_events)").fetchall()
    assert any(item[3] == "environment_id" and item[2] == "environments" for item in foreign_keys)
    catalog.close()


@pytest.mark.parametrize("operation", ["checkout", "sync"])
def test_applied_settings_publication_rolls_back_with_success_event(
    tmp_path: Path, operation: str
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / f"{operation}.sqlite3")
    environment_id = str(uuid.uuid4())
    catalog.create_environment(make_env(environment_id, state="creating"))
    original = catalog.get_environment(environment_id)
    assert original is not None
    replacement = encode_applied_settings(
        python={"selector": "python3", "path": "/venv", "owned": False},
        dependencies={"requirements.txt": "httpx"},
        managed_config={"http_port": "8069"},
        addons=["/addons"],
        git={"ticket": "PROJ-1", "branch": "PROJ-1", "base": "main"},
    )
    catalog._conn.execute(
        f"""CREATE TRIGGER fail_success_event
            BEFORE INSERT ON environment_events
            WHEN NEW.operation = '{operation}'
            BEGIN SELECT RAISE(ABORT, 'injected success-event failure'); END"""
    )
    catalog._conn.commit()

    with pytest.raises(BackupCatalogError, match="injected"):
        if operation == "checkout":
            catalog._finalize_environment_checkout(environment_id, replacement)
        else:
            catalog._record_environment_sync_success(environment_id, replacement)

    row = catalog.get_environment(environment_id)
    assert row is not None
    assert row["state"] == "creating"
    assert row["applied_settings_json"] == LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM environment_events WHERE environment_id = ?",
            (environment_id,),
        ).fetchone()[0]
        == 0
    )
    catalog.close()


def test_v13_claim_and_nullable_restore_provenance_are_transactional(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    claim = catalog._ensure_postgres_cluster_pending(
        "project-a", "odcli_pg_project-a", "pgdata_project-a"
    )
    assert claim.state == "pending"
    assert (
        catalog._ensure_postgres_cluster_pending(
            "project-a", "odcli_pg_project-a", "pgdata_project-a"
        ).cluster_id
        == claim.cluster_id
    )

    with pytest.raises(BackupCatalogError, match="active cluster claim"):
        catalog.record_restore(
            "localhost", 5432, "pending-db", str(uuid.uuid4()), cluster_id=claim.cluster_id
        )

    active = catalog._activate_postgres_cluster(
        claim.cluster_id, "project-a", "odcli_pg_project-a", "pgdata_project-a"
    )
    assert active.state == "active"
    backup_id = str(uuid.uuid4())
    path = tmp_path / "backup.zip"
    path.write_bytes(b"archive")
    catalog.start_download(backup_id, "https://example.test", "source", "zip", False, path)
    catalog.success_download(backup_id, "backup.zip", len(b"archive"), "hash")
    catalog.record_restore(
        "localhost",
        5432,
        "managed-db",
        backup_id,
        cluster_id=active.cluster_id,
        data_directory=tmp_path / "data",
    )
    rows = catalog._conn.execute(
        "SELECT cluster_id, data_directory FROM restores UNION ALL "
        "SELECT cluster_id, data_directory FROM database_events WHERE event_type='restored'"
    ).fetchall()
    assert len(rows) == 2
    assert all(row["cluster_id"] == str(active.cluster_id) for row in rows)
    assert all(row["data_directory"] == str(tmp_path / "data") for row in rows)
    catalog.close()


def test_copy_replacement_publishes_backup_and_restore_atomically(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "replacement.sqlite3")
    claim = catalog._ensure_postgres_cluster_pending(
        "project-a", "odcli_pg_project-a", "pgdata_project-a"
    )
    active = catalog._activate_postgres_cluster(
        claim.cluster_id, "project-a", "odcli_pg_project-a", "pgdata_project-a"
    )
    old_path = tmp_path / "old.zip"
    new_path = tmp_path / "new.zip"
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    old_id = str(uuid.uuid4())
    new_id = str(uuid.uuid4())
    for backup_id, path in ((old_id, old_path), (new_id, new_path)):
        catalog.start_download(backup_id, "https://example.test", "source", "zip", True, path)
        catalog.success_download(backup_id, path.name, path.stat().st_size, "")
    environment_id = str(uuid.uuid4())
    catalog.create_environment(
        make_env(
            environment_id,
            db_mode="copy",
            source_db_name="source",
            target_db_name="copy_target",
            backup_id=old_id,
        )
    )
    catalog.record_restore(
        "localhost",
        5432,
        "copy_target",
        old_id,
        cluster_id=active.cluster_id,
        data_directory=tmp_path / "data",
    )

    catalog._finalize_environment_replacement(
        environment_id,
        new_id,
        db_host="localhost",
        db_port=5432,
        target_database="copy_target",
        cluster_id=active.cluster_id,
        data_directory=tmp_path / "data",
    )

    row = catalog.get_environment(environment_id)
    assert row is not None
    assert row["backup_id"] == new_id
    latest = catalog.latest_restore_provenance("localhost", 5432, "copy_target")
    assert latest is not None
    assert latest.id == uuid.UUID(new_id)
    assert (
        catalog._conn.execute(
            "SELECT operation, outcome FROM environment_events WHERE environment_id=? ORDER BY sequence DESC LIMIT 1",
            (environment_id,),
        ).fetchone()["outcome"]
        == "succeeded"
    )
    catalog.close()


def test_v13_migration_rolls_back_on_claim_index_conflict(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=db)
    catalog._conn.execute("DROP INDEX postgres_clusters_project_idx")
    catalog._conn.execute("PRAGMA user_version = 12")
    catalog._conn.execute("CREATE TABLE postgres_clusters_project_idx (marker INTEGER)")
    catalog._conn.commit()
    catalog.close()

    with pytest.raises(BackupCatalogError):
        BackupCatalog(db_path=db)

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
    assert (
        conn.execute(
            "SELECT type FROM sqlite_master WHERE name='postgres_clusters_project_idx'"
        ).fetchone()[0]
        == "table"
    )
    conn.execute("DROP TABLE postgres_clusters_project_idx")
    conn.commit()
    conn.close()
    reopened = BackupCatalog(db_path=db)
    assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 15
    reopened.close()


def test_v13_migration_rejects_unsupported_cluster_table_shape(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 12")
    conn.execute("CREATE TABLE postgres_clusters (marker INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(BackupCatalogError, match="unsupported shape"):
        BackupCatalog(db_path=db)

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
    assert [row[1] for row in conn.execute("PRAGMA table_info(postgres_clusters)")] == ["marker"]
    conn.close()


def test_v12_backup_order_index_migration_rolls_back_on_conflict(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=db)
    catalog._conn.execute("DROP INDEX backups_point_order_idx")
    catalog._conn.execute("PRAGMA user_version = 11")
    catalog._conn.execute("CREATE TABLE backups_point_order_idx (marker INTEGER)")
    catalog._conn.commit()
    catalog.close()

    with pytest.raises(BackupCatalogError):
        BackupCatalog(db_path=db)

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
    assert (
        conn.execute(
            "SELECT type FROM sqlite_master WHERE name='backups_point_order_idx'"
        ).fetchone()[0]
        == "table"
    )
    conn.execute("DROP TABLE backups_point_order_idx")
    conn.commit()
    conn.close()

    reopened = BackupCatalog(db_path=db)
    assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 15
    assert (
        reopened._conn.execute(
            "SELECT type FROM sqlite_master WHERE name='backups_point_order_idx'"
        ).fetchone()[0]
        == "index"
    )
    reopened.close()


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
    assert catalog._conn.execute("PRAGMA user_version").fetchone()[0] == 15
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
    assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 15
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


@pytest.mark.parametrize(
    "document",
    [
        "{}",
        '{"version": 99, "components": {}}',
        '{"version": 1, "components": {"python": {"status": "unknown"}, "dependencies": {"status": "unknown"}, "odoo": {"status": "known", "values": {"admin_passwd": "raw-secret"}}, "addons": {"status": "unknown"}, "git": {"status": "unknown"}}}',
    ],
)
def test_create_environment_rejects_unsafe_applied_settings(tmp_path: Path, document: str) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    environment: dict[str, JsonValue] = {
        "id": str(uuid.uuid4()),
        "name": "unsafe",
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
        "source_db_name": None,
        "target_db_name": None,
        "backup_id": None,
        "runtime_json": "{}",
        "applied_settings_json": document,
        "state": "creating",
        "created_at": "2026-01-01T00:00:00",
        "last_used_at": None,
        "removed_at": None,
        "last_error": None,
    }
    with pytest.raises(BackupCatalogError, match="invalid applied_settings_json"):
        catalog.create_environment(environment)
    assert catalog._conn.execute("SELECT COUNT(*) FROM environments").fetchone()[0] == 0
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
    assert version == 15
    assert "restore_pending" in schema
    catalog.close()


def test_v8_catalog_upgrades_to_v13_environment_runtime_and_branch_column(tmp_path: Path) -> None:
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
    assert version == 15
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
        CREATE TABLE environment_copy_journal (
            environment_id TEXT PRIMARY KEY REFERENCES environments(id),
            target_database TEXT NOT NULL,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            db_user TEXT,
            backup_id TEXT REFERENCES backups(id),
            stage TEXT NOT NULL CHECK (stage IN ('prepared', 'backed_up', 'restore_pending', 'restored', 'dropped', 'backup_deleted')),
            updated_at TEXT NOT NULL
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
    conn.execute(
        """INSERT INTO environment_copy_journal
           (environment_id, target_database, db_host, db_port, db_user, backup_id, stage, updated_at)
           VALUES (?, 'target', 'localhost', 5432, 'odoo', NULL, 'prepared', '2026-01-01T00:00:00')""",
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
    journal = catalog._conn.execute(
        "SELECT environment_id, target_database FROM environment_copy_journal WHERE environment_id=?",
        (env_id,),
    ).fetchone()

    assert version == 15
    assert "http_port" not in columns
    assert "http_interface" not in columns
    assert "environments_one_active_branch" in indexes
    assert row is not None
    assert row["name"] == "test"
    assert row["branch"] == "main"
    assert event is not None
    assert event["environment_id"] == env_id
    assert journal is not None
    assert journal["environment_id"] == env_id
    assert journal["target_database"] == "target"
    for child_table in ("environment_events", "environment_copy_journal"):
        environment_foreign_tables = {
            foreign_key[2]
            for foreign_key in catalog._conn.execute(f"PRAGMA foreign_key_list({child_table})")
            if foreign_key[3] == "environment_id"
        }
        assert environment_foreign_tables == {"environments"}
    catalog._conn.execute("PRAGMA foreign_keys=ON")
    catalog.add_environment_event(env_id, "use", "succeeded", message="after migration")
    event_ids = [
        event_row[0]
        for event_row in catalog._conn.execute(
            "SELECT environment_id FROM environment_events ORDER BY sequence"
        )
    ]
    assert event_ids == [env_id, env_id]
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
