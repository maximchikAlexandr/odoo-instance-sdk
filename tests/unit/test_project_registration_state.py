from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from odoo_instance_sdk.storage.catalog_migrate import _alembic_config


def _project(tmp_path: Path, name: str) -> tuple[str, Path, Path]:
    root = tmp_path / name
    common = root / ".git"
    common.mkdir(parents=True)
    return f"project_{repo_key(root, common)}", root, common


def test_forget_and_reactivation_keep_one_row_and_existing_timestamps(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_id, root, common = _project(tmp_path, "project")
    catalog._register_project(project_id, root, common)
    before = catalog.get_project(project_id)
    assert before is not None

    assert catalog.forget_project(project_id)
    assert not catalog.forget_project(project_id)
    forgotten = catalog.get_project(project_id)
    assert forgotten is not None
    assert forgotten["registration_state"] == "forgotten"
    assert forgotten["registered_at"] == before["registered_at"]
    assert forgotten["updated_at"] == before["updated_at"]
    assert catalog.list_projects() == []
    assert [row["project_id"] for row in catalog.list_projects(include_forgotten=True)] == [
        project_id
    ]

    assert catalog.reactivate_project(project_id)
    assert not catalog.reactivate_project(project_id)
    active = catalog.get_project(project_id)
    assert active is not None
    assert active["registration_state"] == "registered"
    assert active["registered_at"] == before["registered_at"]
    assert active["updated_at"] == before["updated_at"]
    catalog.close()


def test_forget_is_safe_with_foreign_key_owned_rows(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    project_id, root, common = _project(tmp_path, "project")
    catalog._register_project(project_id, root, common)
    catalog._conn.execute(
        """INSERT INTO backups
           (id, source_base_url, database_name, format, filestore_requested, state, started_at,
            project_id)
           VALUES ('backup', 'https://example.test', 'db', 'zip', 0, 'available',
                   datetime('now'), ?)""",
        (project_id,),
    )
    catalog._conn.commit()

    assert catalog.forget_project(project_id)
    assert (
        catalog._conn.execute("SELECT project_id FROM backups WHERE id = 'backup'").fetchone()[0]
        == project_id
    )
    catalog.close()


def test_monitor_snapshot_hides_forgotten_projects_and_keeps_lifecycle_lookup(
    tmp_path: Path,
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    active_id, active_root, active_common = _project(tmp_path, "active")
    forgotten_id, forgotten_root, forgotten_common = _project(tmp_path, "forgotten")
    catalog._register_project(active_id, active_root, active_common)
    catalog._register_project(forgotten_id, forgotten_root, forgotten_common)
    catalog.create_environment(
        {
            "id": "forgotten-environment",
            "name": "forgotten",
            "repository_root": str(forgotten_root),
            "git_common_dir": str(forgotten_common),
            "branch": "main",
            "base_ref": "HEAD",
            "worktree_path": str(forgotten_root / "worktree"),
            "generated_config_path": str(forgotten_root / "odoo.conf"),
            "python_environment_path": str(forgotten_root / ".venv"),
            "python_environment_owned": False,
            "dependency_lock_path": str(forgotten_root / "lock"),
            "db_mode": "shared",
            "runtime_json": "{}",
            "state": "ready",
            "created_at": "2026-01-01T00:00:00",
            "project_id": forgotten_id,
        }
    )
    assert catalog.forget_project(forgotten_id)

    rows = catalog._monitor_snapshot_rows()
    assert [row["project_id"] for row in rows.projects] == [active_id]
    assert rows.environments == ()
    assert catalog.get_project(forgotten_id)["registration_state"] == "forgotten"  # type: ignore[index]
    catalog.close()


def test_registration_state_downgrade_refuses_forgotten_rows(tmp_path: Path) -> None:
    db = tmp_path / "catalog.sqlite3"
    catalog = BackupCatalog(db_path=db)
    project_id, root, common = _project(tmp_path, "project")
    catalog._register_project(project_id, root, common)
    assert catalog.forget_project(project_id)
    catalog.close()

    with pytest.raises(RuntimeError, match="forgotten rows"):
        command.downgrade(_alembic_config(db), "0008")

    conn = sqlite3.connect(str(db))
    assert "registration_state" in {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    conn.close()


def test_project_lifecycle_unknown_id_is_explicit(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    with pytest.raises(BackupCatalogError, match="not registered"):
        catalog.forget_project("project_missing")
    with pytest.raises(BackupCatalogError, match="not registered"):
        catalog.reactivate_project("project_missing")
    catalog.close()
