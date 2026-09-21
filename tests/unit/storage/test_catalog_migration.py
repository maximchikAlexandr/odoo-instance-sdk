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
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from odoo_instance_sdk.storage.catalog_migrate import CATALOG_REVISION, catalog_revision
from odoo_instance_sdk.storage.catalog_schema import CATALOG_INDEXES, CATALOG_TABLES
from tests.unit.monitor_support import make_env
from tests.unit.storage.catalog_alpha_fixture import write_alpha_catalog

V16_CATALOG_FIXTURE = Path(__file__).parents[2] / "fixtures" / "catalog_v16.sql"


def _assert_current_revision(conn: sqlite3.Connection) -> None:
    assert catalog_revision(conn) == CATALOG_REVISION


def test_fresh_install_creates_current_schema_directly(tmp_path: Path) -> None:
    durable = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=durable)
    _assert_current_revision(catalog._conn)
    backup_columns = {row[1] for row in catalog._conn.execute("PRAGMA table_info(backups)")}
    assert "source_git_branch" in backup_columns
    assert "project_id" in backup_columns
    environment_columns = {
        row[1] for row in catalog._conn.execute("PRAGMA table_info(environments)")
    }
    assert "applied_settings_json" in environment_columns
    tables = {
        row[0] for row in catalog._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert set(CATALOG_TABLES) <= tables
    assert "runtime" in tables
    for child_table in ("environment_events", "environment_copy_journal"):
        environment_foreign_keys = {
            (foreign_key[3], foreign_key[2], foreign_key[4])
            for foreign_key in catalog._conn.execute(f"PRAGMA foreign_key_list({child_table})")
        }
        assert ("environment_id", "environments", "id") in environment_foreign_keys
    indexes = {row[1] for row in catalog._conn.execute("PRAGMA index_list(backups)")}
    assert "backups_point_order_idx" in indexes
    catalog.close()


def test_alpha_catalogue_is_backed_up_stamped_and_preserves_rows(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    backup_id, environment_id, project_id = write_alpha_catalog(db)
    backup_copy = db.with_suffix(f"{db.suffix}.pre-alembic.backup")
    assert not backup_copy.exists()

    catalog = BackupCatalog(db_path=db)
    _assert_current_revision(catalog._conn)
    assert backup_copy.exists()
    row = catalog.get_by_id(backup_id)
    assert row is not None
    assert row["project_id"] == project_id
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM backup_events WHERE backup_id = ?", (backup_id,)
        ).fetchone()[0]
        == 1
    )
    assert catalog.get_environment(environment_id) is not None
    catalog.close()


def test_real_v16_catalogue_is_repaired_stamped_and_preserves_rows(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript(V16_CATALOG_FIXTURE.read_text())
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in CATALOG_TABLES
    }
    conn.close()

    catalog = BackupCatalog(db_path=db)

    _assert_current_revision(catalog._conn)
    after = {
        table: catalog._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in CATALOG_TABLES
    }
    assert after == before
    assert catalog.get_by_id("backup-v16") is not None
    assert catalog.get_environment("environment-v16") is not None
    assert catalog._conn.execute(
        "SELECT 1 FROM runtime WHERE owner_kind = 'environment' AND owner_id = 'environment-v16'"
    ).fetchone()
    indexes = {
        row[0]
        for row in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert "environments_one_active_branch" in indexes
    catalog.close()


def test_real_v16_catalogue_rejects_duplicate_active_branch(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript(V16_CATALOG_FIXTURE.read_text())
    conn.execute(
        """INSERT INTO environments
           SELECT 'environment-v16-duplicate', 'duplicate', repository_root, git_common_dir,
                  branch, base_ref, '/worktree-duplicate', '/worktree-duplicate/odoo.conf',
                  python_environment_path, python_environment_owned, dependency_lock_path,
                  db_mode, source_db_name, 'alpha_copy_duplicate', backup_id, runtime_json,
                  state, created_at, last_used_at, removed_at, last_error, applied_settings_json
           FROM environments WHERE id = 'environment-v16'"""
    )
    conn.commit()
    conn.close()

    with pytest.raises(
        BackupCatalogError,
        match="multiple active environments for the same branch",
    ):
        BackupCatalog(db_path=db)

    conn = sqlite3.connect(str(db))
    assert catalog_revision(conn) is None
    assert (
        conn.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='index' AND name='environments_one_active_branch'"
        ).fetchone()[0]
        == 0
    )
    conn.close()


def test_reopen_stamped_catalog_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=db)
    catalog.close()
    reopened = BackupCatalog(db_path=db)
    _assert_current_revision(reopened._conn)
    reopened.close()


def test_schema_verification_failure_does_not_stamp(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE backups (
            id TEXT PRIMARY KEY,
            source_base_url TEXT NOT NULL,
            database_name TEXT NOT NULL,
            state TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(BackupCatalogError, match="schema is not equivalent"):
        BackupCatalog(db_path=db)

    conn = sqlite3.connect(str(db))
    assert catalog_revision(conn) is None
    conn.close()


def test_foreign_keys_and_indexes_exist_after_fresh_install(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    indexes = {
        row[0]
        for row in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert set(CATALOG_INDEXES) <= indexes
    foreign_keys = {
        (item[3], item[2], item[4])
        for item in catalog._conn.execute("PRAGMA foreign_key_list(backups)")
    }
    assert ("project_id", "projects", "project_id") in foreign_keys
    view = catalog._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='environment_runtime'"
    ).fetchone()
    assert view is not None
    catalog.close()


def test_data_writes_after_alpha_upgrade(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    backup_id, environment_id, _project_id = write_alpha_catalog(db)
    catalog = BackupCatalog(db_path=db)
    catalog.start_download(
        str(uuid.uuid4()),
        "https://example.test",
        "fresh",
        "zip",
        True,
        tmp_path / "fresh.zip",
        project_id="project_alpha-fixture",
    )
    catalog.add_environment_event(environment_id, "use", "succeeded", message="after upgrade")
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM backup_events WHERE backup_id = ?", (backup_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM environment_events WHERE environment_id = ?",
            (environment_id,),
        ).fetchone()[0]
        == 2
    )
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


def test_fresh_catalog_keeps_one_active_environment_per_branch(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    env_id = str(uuid.uuid4())
    catalog.create_environment(make_env(env_id, state="ready"))
    with pytest.raises(BackupCatalogError):
        catalog.create_environment(
            make_env(
                str(uuid.uuid4()),
                branch="main",
                worktree_path="/wt2",
                generated_config_path="/wt2/odoo.conf",
                state="creating",
            )
        )
    catalog.close()
