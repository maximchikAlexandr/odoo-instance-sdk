from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.storage.backup_catalog import CURRENT_SCHEMA_VERSION, BackupCatalog


def _make_env(env_id: str) -> dict[str, object]:
    return {
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
        "state": "ready",
        "created_at": "2026-01-01T00:00:00",
        "last_used_at": None,
        "removed_at": None,
        "last_error": None,
    }


def _runtime_kwargs() -> dict[str, object]:
    return {
        "root_pid": 12345,
        "create_time": 1700000000.0,
        "started_at": "2026-01-01T00:00:00",
        "checkout_branch": "main",
        "commit_sha": "abc123def456",
        "http_url": "http://127.0.0.1:8069",
        "http_port": 8069,
        "database_name": "mydb",
    }


def test_fresh_catalog_has_schema_v9_and_runtime_table(tmp_path: Path) -> None:
    assert CURRENT_SCHEMA_VERSION == 9
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    version = catalog._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 9
    tables = {
        r[0]
        for r in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "environment_runtime" in tables
    catalog.close()


def test_reopen_v9_catalog_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=db)
    catalog.close()
    reopened = BackupCatalog(db_path=db)
    version = reopened._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 9
    reopened.close()


def test_v8_catalog_upgrades_to_v9_on_open(tmp_path: Path) -> None:
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
    assert version == 9
    assert "environment_runtime" in tables
    catalog.close()


def test_upsert_inserts_then_updates_single_row(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    env_id = str(uuid.uuid4())
    catalog.create_environment(_make_env(env_id))

    catalog.upsert_environment_runtime(env_id, **_runtime_kwargs())  # type: ignore[arg-type]
    row = catalog.get_environment_runtime(env_id)
    assert row is not None
    assert row["root_pid"] == 12345
    assert row["http_port"] == 8069

    updated = {**_runtime_kwargs(), "root_pid": 99999, "http_port": 8100}
    catalog.upsert_environment_runtime(env_id, **updated)  # type: ignore[arg-type]
    rows = catalog._conn.execute(
        "SELECT * FROM environment_runtime WHERE environment_id = ?", (env_id,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["root_pid"] == 99999
    assert rows[0]["http_port"] == 8100
    catalog.close()


def test_clear_removes_row_and_is_idempotent(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    env_id = str(uuid.uuid4())
    catalog.create_environment(_make_env(env_id))
    catalog.upsert_environment_runtime(env_id, **_runtime_kwargs())  # type: ignore[arg-type]
    assert catalog.get_environment_runtime(env_id) is not None

    catalog.clear_environment_runtime(env_id)
    assert catalog.get_environment_runtime(env_id) is None

    # idempotent: no error on absent row
    catalog.clear_environment_runtime(env_id)
    catalog.close()


def test_upsert_on_missing_environment_raises_fk(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    with pytest.raises(BackupCatalogError):
        catalog.upsert_environment_runtime("no-such-env", **_runtime_kwargs())  # type: ignore[arg-type]
    catalog.close()


def test_readonly_api_returns_row_with_columns(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    env_id = str(uuid.uuid4())
    catalog.create_environment(_make_env(env_id))
    catalog.upsert_environment_runtime(env_id, **_runtime_kwargs())  # type: ignore[arg-type]

    row = catalog.get_environment_runtime(env_id)
    assert row is not None
    assert row["environment_id"] == env_id
    assert row["checkout_branch"] == "main"
    assert row["commit_sha"] == "abc123def456"
    assert row["http_url"] == "http://127.0.0.1:8069"
    assert row["database_name"] == "mydb"
    assert row["updated_at"] is not None

    listed = catalog.list_environment_runtimes()
    assert len(listed) == 1
    assert listed[0]["environment_id"] == env_id
    catalog.close()


def test_list_environment_runtimes_ordered(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    env_a = "aaa-aaa"
    env_b = "bbb-bbb"
    catalog.create_environment(_make_env(env_a))
    catalog.create_environment(
        {**_make_env(env_b), "name": "b", "git_common_dir": "/repo2/.git", "branch": "dev"}  # type: ignore[arg-type]
    )
    catalog.upsert_environment_runtime(env_b, **_runtime_kwargs())  # type: ignore[arg-type]
    catalog.upsert_environment_runtime(env_a, **_runtime_kwargs())  # type: ignore[arg-type]

    listed = catalog.list_environment_runtimes()
    assert [r["environment_id"] for r in listed] == [env_a, env_b]
    catalog.close()
