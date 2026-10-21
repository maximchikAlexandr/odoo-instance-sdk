from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import cast

import pytest
from alembic import command

from odoo_instance_sdk.exceptions import BackupCatalogError, LockConflictError
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.resource_inventory import collect_resource_inventory
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.project_init import init_project, init_project_command
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from odoo_instance_sdk.storage.catalog_migrate import _alembic_config


def _project(tmp_path: Path, name: str) -> tuple[str, Path, Path]:
    root = tmp_path / name
    common = root / ".git"
    common.mkdir(parents=True)
    return f"project_{repo_key(root, common)}", root, common


def test_public_import_order_does_not_cycle() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from odoo_instance_sdk.storage.backup_catalog import BackupCatalog; "
                "from odoo_instance_sdk.project_init import init_project; "
                "assert BackupCatalog and init_project"
            ),
        ],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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


def _seed_forgotten_project(catalog: BackupCatalog, root: Path) -> str:
    common = root / ".git"
    common.mkdir(parents=True)
    project_id = f"project_{repo_key(root, common)}"
    catalog._register_project(project_id, root, common)
    assert catalog.forget_project(project_id)
    return project_id


def test_init_reactivates_only_after_success_and_dry_run_is_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_: catalog_path
    )
    root = tmp_path / "project"
    root.mkdir()
    catalog = BackupCatalog(db_path=catalog_path)
    project_id = _seed_forgotten_project(catalog, root)
    catalog.close()

    result = init_project(
        root,
        ProjectConfig(repository_root=root, python="3.12"),
        postgres_allocated=False,
        allow_partial=True,
    )
    assert result["python"] == "3.12"
    catalog = BackupCatalog(db_path=catalog_path)
    assert catalog.get_project(project_id)["registration_state"] == "registered"  # type: ignore[index]
    catalog.close()

    dry_root = tmp_path / "dry-run"
    dry_root.mkdir()
    dry_catalog = BackupCatalog(db_path=catalog_path)
    dry_result = init_project(
        dry_root,
        ProjectConfig(repository_root=dry_root, python="3.12"),
        postgres_allocated=False,
        allow_partial=True,
        dry_run=True,
    )
    assert dry_result["python"] == "3.12"
    assert not (dry_root / ".odcli" / "project.toml").exists()
    assert [row["project_id"] for row in dry_catalog.list_projects()] == [project_id]
    dry_catalog.close()


def test_init_lifecycle_lock_serializes_remove_until_postconditions_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path", lambda **_: catalog_path
    )
    root = tmp_path / "project"
    root.mkdir()
    catalog = BackupCatalog(db_path=catalog_path)
    project_id = _seed_forgotten_project(catalog, root)
    catalog.close()

    entered = threading.Event()
    release = threading.Event()
    original = __import__("odoo_instance_sdk.project_init", fromlist=["_execute_manifest_phase"])
    execute_manifest_phase = original._execute_manifest_phase

    def blocked(context: object, **kwargs: object) -> dict[str, object]:
        entered.set()
        assert release.wait(timeout=5)
        return cast("dict[str, object]", execute_manifest_phase(context, **kwargs))

    monkeypatch.setattr(original, "_execute_manifest_phase", blocked)
    command = init_project_command(
        root,
        ProjectConfig(repository_root=root, python="3.12"),
        postgres_allocated=False,
        allow_partial=True,
        no_input=True,
    )
    errors: list[BaseException] = []

    def run_init() -> None:
        try:
            command.run()
        except BaseException as exc:  # pragma: no cover - assertion below reports failures
            errors.append(exc)

    thread = threading.Thread(target=run_init)
    thread.start()
    assert entered.wait(timeout=5)
    removing = BackupCatalog(db_path=catalog_path)
    with pytest.raises(LockConflictError):
        removing.forget_project(project_id)
    release.set()
    thread.join(timeout=5)
    assert not errors
    assert not thread.is_alive()
    assert removing.forget_project(project_id)
    removing.close()


def test_legacy_global_reads_match_registered_repository_and_hide_forgotten(
    tmp_path: Path,
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    active_id, active_root, active_common = _project(tmp_path, "active")
    forgotten_id, forgotten_root, forgotten_common = _project(tmp_path, "forgotten")
    catalog._register_project(active_id, active_root, active_common)
    catalog._register_project(forgotten_id, forgotten_root, forgotten_common)

    def add_legacy(environment_id: str, root: Path, common: Path) -> None:
        catalog.create_environment(
            {
                "id": environment_id,
                "name": environment_id,
                "repository_root": str(root),
                "git_common_dir": str(common),
                "branch": "main",
                "base_ref": "HEAD",
                "worktree_path": str(root / "worktree"),
                "generated_config_path": str(root / "odoo.conf"),
                "python_environment_path": str(root / ".venv"),
                "python_environment_owned": False,
                "dependency_lock_path": str(root / "lock"),
                "db_mode": "shared",
                "runtime_json": "{}",
                "state": "ready",
                "created_at": "2026-01-01T00:00:00",
            }
        )
        catalog._upsert_runtime(
            "environment",
            environment_id,
            root_pid=1,
            create_time=1.0,
            started_at="2026-01-01T00:00:00",
            checkout_branch="main",
            commit_sha="abc",
            http_url="http://127.0.0.1:8069",
            http_port=8069,
            database_name="db",
        )

    add_legacy("active-legacy", active_root, active_common)
    add_legacy("forgotten-legacy", forgotten_root, forgotten_common)
    add_legacy("orphan-legacy", tmp_path / "orphan", tmp_path / "orphan.git")
    assert catalog.forget_project(forgotten_id)

    global_rows = catalog._monitor_snapshot_rows()
    assert [row["id"] for row, _ in global_rows.environments] == [
        "orphan-legacy",
        "active-legacy",
    ]
    assert [row["owner_id"] for row in global_rows.project_runtimes] == []
    assert {
        runtime["owner_id"] for _, runtime in global_rows.environments if runtime is not None
    } == {
        "active-legacy",
        "orphan-legacy",
    }
    project_rows = catalog._monitor_snapshot_rows(project_id=active_id)
    assert [row["id"] for row, _ in project_rows.environments] == ["active-legacy"]
    assert [row["id"] for row in catalog.list_environments()] == [
        "orphan-legacy",
        "active-legacy",
    ]
    catalog.close()


def test_project_scoped_legacy_reads_require_matching_repository_identity(
    tmp_path: Path,
) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    alpha_root = tmp_path / "alpha"
    beta_root = tmp_path / "beta"
    alpha_common = alpha_root / ".git"
    beta_common = beta_root / ".git"
    alpha_common.mkdir(parents=True)
    beta_common.mkdir(parents=True)
    alpha_id = f"project_{repo_key(alpha_root, alpha_common)}"
    beta_id = f"project_{repo_key(beta_root, beta_common)}"

    def add_legacy(environment_id: str, root: Path, common: Path) -> None:
        catalog.create_environment(
            {
                "id": environment_id,
                "name": environment_id,
                "repository_root": str(root),
                "git_common_dir": str(common),
                "branch": "main",
                "base_ref": "HEAD",
                "worktree_path": str(root / "worktree"),
                "generated_config_path": str(root / "odoo.conf"),
                "python_environment_path": str(root / ".venv"),
                "python_environment_owned": False,
                "dependency_lock_path": str(root / "lock"),
                "db_mode": "shared",
                "runtime_json": "{}",
                "state": "ready",
                "created_at": "2026-01-01T00:00:00",
            }
        )

    add_legacy("alpha-legacy", alpha_root, alpha_common)
    add_legacy("beta-legacy", beta_root, beta_common)

    alpha_snapshot = catalog._monitor_snapshot_rows(
        project_id=alpha_id,
        repository_root=str(alpha_root),
        git_common_dir=str(alpha_common),
    )
    beta_snapshot = catalog._monitor_snapshot_rows(
        project_id=beta_id,
        repository_root=str(beta_root),
        git_common_dir=str(beta_common),
    )
    unknown_snapshot = catalog._monitor_snapshot_rows(project_id="project_unknown")
    assert [row["id"] for row, _runtime in alpha_snapshot.environments] == ["alpha-legacy"]
    assert [row["id"] for row, _runtime in beta_snapshot.environments] == ["beta-legacy"]
    assert unknown_snapshot.environments == ()

    alpha_inventory = collect_resource_inventory(
        catalog=catalog,
        project_id=alpha_id,
        repository_root=str(alpha_root),
        git_common_dir=str(alpha_common),
    )
    unknown_inventory = collect_resource_inventory(catalog=catalog, project_id="project_unknown")
    alpha_environment_ids = {
        item.stable_identity
        for item in alpha_inventory.resources
        if item.type.value == "environment"
    }
    unknown_environment_ids = {
        item.stable_identity
        for item in unknown_inventory.resources
        if item.type.value == "environment"
    }
    assert alpha_environment_ids == {"environment:alpha-legacy"}
    assert unknown_environment_ids == set()
    catalog.close()
