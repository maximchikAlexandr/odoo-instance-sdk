from __future__ import annotations

import uuid
from pathlib import Path
from typing import TypedDict

import pytest

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, CatalogValue
from odoo_instance_sdk.storage.catalog_migrate import CATALOG_REVISION, catalog_revision


def _make_env(env_id: str) -> dict[str, CatalogValue]:
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


class RuntimeKwargs(TypedDict):
    root_pid: int
    create_time: float
    started_at: str
    checkout_branch: str
    commit_sha: str
    http_url: str
    http_port: int
    database_name: str


def _runtime_kwargs() -> RuntimeKwargs:
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


def test_fresh_catalog_has_latest_schema_and_runtime_table(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    assert catalog_revision(catalog._conn) == CATALOG_REVISION
    tables = {
        r[0]
        for r in catalog._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "runtime" in tables
    assert "projects" in tables
    catalog.close()


def test_reopen_catalog_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=db)
    catalog.close()
    reopened = BackupCatalog(db_path=db)
    assert catalog_revision(reopened._conn) == CATALOG_REVISION
    reopened.close()


def test_upsert_inserts_then_updates_single_row(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    env_id = str(uuid.uuid4())
    catalog.create_environment(_make_env(env_id))

    catalog.upsert_environment_runtime(env_id, **_runtime_kwargs())
    row = catalog.get_environment_runtime(env_id)
    assert row is not None
    assert row["root_pid"] == 12345
    assert row["http_port"] == 8069

    updated: RuntimeKwargs = {**_runtime_kwargs(), "root_pid": 99999, "http_port": 8100}
    catalog.upsert_environment_runtime(env_id, **updated)
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
    catalog.upsert_environment_runtime(env_id, **_runtime_kwargs())
    assert catalog.get_environment_runtime(env_id) is not None

    catalog.clear_environment_runtime(env_id)
    assert catalog.get_environment_runtime(env_id) is None

    # idempotent: no error on absent row
    catalog.clear_environment_runtime(env_id)
    catalog.close()


def test_upsert_on_missing_environment_raises_fk(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    with pytest.raises(BackupCatalogError):
        catalog.upsert_environment_runtime("no-such-env", **_runtime_kwargs())
    catalog.close()


def test_readonly_api_returns_row_with_columns(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    env_id = str(uuid.uuid4())
    catalog.create_environment(_make_env(env_id))
    catalog.upsert_environment_runtime(env_id, **_runtime_kwargs())

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
        {**_make_env(env_b), "name": "b", "git_common_dir": "/repo2/.git", "branch": "dev"}
    )
    catalog.upsert_environment_runtime(env_b, **_runtime_kwargs())
    catalog.upsert_environment_runtime(env_a, **_runtime_kwargs())

    listed = catalog.list_environment_runtimes()
    assert [r["environment_id"] for r in listed] == [env_a, env_b]
    catalog.close()


def test_project_runtime_uses_exclusive_owner_and_registration(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    root = tmp_path / "repo"
    common = root / ".git"
    common.mkdir(parents=True)
    project_id = f"project_{repo_key(root, common)}"

    catalog._register_project(project_id, root, common)
    catalog._upsert_runtime("project", project_id, **_runtime_kwargs())

    row = catalog._conn.execute(
        "SELECT * FROM runtime WHERE owner_kind='project' AND owner_id=?", (project_id,)
    ).fetchone()
    assert row is not None
    assert row["owner_kind"] == "project"
    assert row["owner_id"] == project_id
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM runtime WHERE owner_id=?", (project_id,)
        ).fetchone()[0]
        == 1
    )

    catalog._clear_runtime("project", project_id)
    assert (
        catalog._conn.execute(
            "SELECT COUNT(*) FROM runtime WHERE owner_id=?", (project_id,)
        ).fetchone()[0]
        == 0
    )
    catalog.close()


def test_conditional_runtime_clear_is_owner_neutral_and_preserves_project_registration(
    tmp_path: Path,
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    root = tmp_path / "repo"
    common = root / ".git"
    common.mkdir(parents=True)
    project_id = f"project_{repo_key(root, common)}"
    catalog._register_project(project_id, root, common)
    catalog._upsert_runtime("project", project_id, **_runtime_kwargs())

    assert catalog._clear_runtime_if_matches(
        "project", project_id, root_pid=12345, create_time=1700000000.0
    )
    assert catalog.get_runtime("project", project_id) is None
    assert (
        catalog._conn.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        is not None
    )
    assert not catalog._clear_runtime_if_matches(
        "project", project_id, root_pid=12345, create_time=1700000000.0
    )
    catalog.close()


@pytest.mark.parametrize("mismatch", ["owner", "root_pid", "create_time"])
def test_conditional_runtime_clear_preserves_replacement_row_and_project_registration(
    tmp_path: Path, mismatch: str
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    root = tmp_path / "repo"
    common = root / ".git"
    common.mkdir(parents=True)
    project_id = f"project_{repo_key(root, common)}"
    catalog._register_project(project_id, root, common)
    catalog._upsert_runtime("project", project_id, **_runtime_kwargs())

    replacement = _runtime_kwargs()
    replacement["root_pid"] = 54321
    replacement["create_time"] = 1800000000.0
    if mismatch == "owner":
        replacement_root = tmp_path / "replacement-repo"
        replacement_common = replacement_root / ".git"
        replacement_common.mkdir(parents=True)
        replacement_id = f"project_{repo_key(replacement_root, replacement_common)}"
        catalog._register_project(replacement_id, replacement_root, replacement_common)
        catalog._upsert_runtime("project", replacement_id, **replacement)
        expected_owner = replacement_id
        expected_pid = 12345
        expected_create_time = 1700000000.0
    else:
        catalog._upsert_runtime("project", project_id, **replacement)
        expected_owner = project_id
        expected_pid = 12345 if mismatch == "root_pid" else 54321
        expected_create_time = 1700000000.0 if mismatch == "create_time" else 1800000000.0

    assert not catalog._clear_runtime_if_matches(
        "project",
        expected_owner,
        root_pid=expected_pid,
        create_time=expected_create_time,
    )
    row = catalog.get_runtime("project", replacement_id if mismatch == "owner" else project_id)
    assert row is not None
    assert row["root_pid"] == 54321
    assert row["create_time"] == 1800000000.0
    assert (
        catalog._conn.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        is not None
    )
    catalog.close()


def test_runtime_owner_validation_rejects_invalid_and_missing_owners(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")

    with pytest.raises(BackupCatalogError, match="exactly environment or project"):
        catalog._upsert_runtime("invalid", "owner", **_runtime_kwargs())
    with pytest.raises(BackupCatalogError, match="exactly environment or project"):
        catalog._upsert_runtime("environment", " ", **_runtime_kwargs())
    with pytest.raises(BackupCatalogError, match="not registered"):
        catalog._upsert_runtime("project", "project_missing", **_runtime_kwargs())
    with pytest.raises(BackupCatalogError, match="exactly environment or project"):
        catalog._clear_runtime("invalid", "owner")
    with pytest.raises(BackupCatalogError, match="exactly environment or project"):
        catalog.get_runtime("invalid", "owner")
    with pytest.raises(BackupCatalogError, match="exactly environment or project"):
        catalog._clear_runtime_if_matches(
            "invalid", "owner", root_pid=12345, create_time=1700000000.0
        )

    catalog.close()
