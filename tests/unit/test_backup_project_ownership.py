from __future__ import annotations

import uuid
from pathlib import Path

from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _project(catalog: BackupCatalog, root: Path, name: str) -> str:
    repository = root / name
    common = repository / ".git"
    common.mkdir(parents=True)
    project_id = f"project_{repo_key(repository, common)}"
    catalog._register_project(project_id, repository, common)
    return project_id


def _backup(catalog: BackupCatalog, root: Path, project_id: str | None) -> str:
    backup_id = str(uuid.uuid4())
    path = root / f"{backup_id}.zip"
    path.write_bytes(b"backup")
    catalog.start_download(
        backup_id,
        "https://odoo.example",
        "database",
        "zip",
        True,
        path,
        project_id=project_id,
    )
    catalog.success_download(backup_id, path.name, path.stat().st_size, "digest")
    return backup_id


def _environment(catalog: BackupCatalog, root: Path, name: str, backup_id: str) -> None:
    repository = root / name
    environment_id = str(uuid.uuid4())
    catalog.create_environment(
        {
            "id": environment_id,
            "name": name,
            "repository_root": str(repository),
            "git_common_dir": str(repository / ".git"),
            "branch": "main",
            "base_ref": "HEAD",
            "worktree_path": str(repository),
            "generated_config_path": str(repository / "odoo.conf"),
            "python_environment_path": str(repository / "venv"),
            "python_environment_owned": False,
            "dependency_lock_path": str(repository / "requirements.lock"),
            "db_mode": "shared",
            "source_db_name": "database",
            "target_db_name": None,
            "backup_id": backup_id,
            "runtime_json": "{}",
            "state": "ready",
            "created_at": "2026-01-01T00:00:00",
            "last_used_at": None,
            "removed_at": None,
            "last_error": None,
        }
    )


def test_download_persists_direct_project_ownership_and_scoped_queries(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    first = _project(catalog, tmp_path, "first")
    second = _project(catalog, tmp_path, "second")
    first_backup = _backup(catalog, tmp_path, first)
    second_backup = _backup(catalog, tmp_path, second)

    assert catalog.get_by_id(first_backup)["project_id"] == first  # type: ignore[index]
    scoped = catalog._list_backup_projections(project_id=first, include_all_states=True)
    assert [str(item.backup.id) for item in scoped.items] == [first_backup]
    global_rows = catalog._list_backup_projections(include_all_states=True)
    assert {str(item.backup.id) for item in global_rows.items} == {first_backup, second_backup}
    catalog.close()


def test_ambiguous_legacy_backup_stays_global_and_unowned(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    first = _project(catalog, tmp_path, "first")
    second = _project(catalog, tmp_path, "second")
    backup_id = _backup(catalog, tmp_path, None)
    _environment(catalog, tmp_path, "first", backup_id)
    _environment(catalog, tmp_path, "second", backup_id)

    assert catalog.get_by_id(backup_id)["project_id"] is None  # type: ignore[index]
    assert catalog._list_backup_projections(project_id=first, include_all_states=True).items == ()
    assert catalog._list_backup_projections(project_id=second, include_all_states=True).items == ()
    global_rows = catalog._list_backup_projections(include_all_states=True)
    assert [str(item.backup.id) for item in global_rows.items] == [backup_id]
    catalog.close()


def test_legacy_backup_is_backfilled_only_for_one_deterministic_owner(tmp_path: Path) -> None:
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    owner = _project(catalog, tmp_path, "owner")
    backup_id = _backup(catalog, tmp_path, None)
    environment_id = str(uuid.uuid4())
    catalog.create_environment(
        {
            "id": environment_id,
            "name": "owner-env",
            "repository_root": str(tmp_path / "owner"),
            "git_common_dir": str(tmp_path / "owner" / ".git"),
            "branch": "main",
            "base_ref": "HEAD",
            "worktree_path": str(tmp_path),
            "generated_config_path": str(tmp_path / "odoo.conf"),
            "python_environment_path": str(tmp_path / "venv"),
            "python_environment_owned": False,
            "dependency_lock_path": str(tmp_path / "requirements.lock"),
            "db_mode": "shared",
            "source_db_name": "database",
            "target_db_name": None,
            "backup_id": backup_id,
            "runtime_json": "{}",
            "state": "ready",
            "created_at": "2026-01-01T00:00:00",
            "last_used_at": None,
            "removed_at": None,
            "last_error": None,
        }
    )

    catalog._list_backup_projections(project_id=owner, include_all_states=True)
    assert catalog.get_by_id(backup_id)["project_id"] == owner  # type: ignore[index]
    catalog.close()
