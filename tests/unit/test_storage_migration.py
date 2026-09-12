from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.storage_migration import (
    StorageMigrationConflictError,
    StorageMigrationError,
    migrate_storage,
)


def _legacy_tree(tmp_path: Path) -> dict[str, Path]:
    roots = {name: tmp_path / f"legacy-{name}" for name in ("config", "data", "cache", "state")}
    for root in roots.values():
        root.mkdir()
    return roots


def test_fresh_migration_uses_one_root_and_leaves_repository_odcli_alone(tmp_path: Path) -> None:
    home = tmp_path / "home with spaces"
    home.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    local_state = repository / ".odcli" / "project.toml"
    local_state.parent.mkdir()
    local_state.write_text("project = true", encoding="utf-8")

    result = migrate_storage(home=home, legacy=_legacy_tree(tmp_path))

    assert result.state == "complete"
    root = home / ".odcli"
    assert root.is_dir()
    assert local_state.read_text(encoding="utf-8") == "project = true"
    assert (root / "storage-migration.json").is_file()
    assert (root / ".storage-migration.lock").is_file()


def test_migration_preserves_paths_and_modes_for_external_legacy_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy = _legacy_tree(tmp_path)
    backup = legacy["cache"] / "backups" / "backup.zip"
    backup.parent.mkdir()
    backup.write_bytes(b"backup")
    backup.chmod(0o640)

    catalog_path = legacy["data"] / "catalog.sqlite3"
    connection = sqlite3.connect(catalog_path)
    connection.execute("CREATE TABLE backups (id TEXT PRIMARY KEY, path TEXT)")
    connection.execute("INSERT INTO backups VALUES ('backup', ?)", (str(backup),))
    connection.commit()
    connection.close()

    migrate_storage(home=home, legacy=legacy)
    destination = home / ".odcli"

    assert (destination / "backups" / "backup.zip").read_bytes() == b"backup"
    assert (destination / "backups" / "backup.zip").stat().st_mode & 0o777 == 0o640
    connection = sqlite3.connect(destination / "catalog.sqlite3")
    assert connection.execute("SELECT path FROM backups").fetchone()[0] == str(
        destination / "backups" / "backup.zip"
    )
    connection.close()
    assert not backup.exists()


def test_conflicting_destination_is_fail_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy = _legacy_tree(tmp_path)
    source = legacy["cache"] / "backups" / "backup.zip"
    source.parent.mkdir()
    source.write_bytes(b"legacy")
    destination = home / ".odcli" / "backups" / "backup.zip"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"different")

    with pytest.raises(StorageMigrationConflictError) as error:
        migrate_storage(home=home, legacy=legacy)

    assert destination.read_bytes() == b"different"
    assert source.read_bytes() == b"legacy"
    assert destination in error.value.paths


def test_interrupted_copy_is_resumable_without_duplication(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy = _legacy_tree(tmp_path)
    source = legacy["data"] / "projects" / "project" / "state"
    source.parent.mkdir(parents=True)
    source.write_text("state", encoding="utf-8")

    def interrupt(stage: str) -> None:
        if stage == "copy":
            raise RuntimeError("controlled interruption")

    with pytest.raises(RuntimeError, match="controlled interruption"):
        migrate_storage(home=home, legacy=legacy, after_stage=interrupt)

    assert source.exists()
    assert (home / ".odcli" / "projects" / "project" / "state").exists()
    migrate_storage(home=home, legacy=legacy)
    assert not source.exists()
    assert (home / ".odcli" / "projects" / "project" / "state").read_text() == "state"


def test_interrupted_rewrite_is_resumable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy = _legacy_tree(tmp_path)
    source = legacy["cache"] / "backups" / "backup.zip"
    source.parent.mkdir()
    source.write_bytes(b"backup")
    catalog = legacy["data"] / "catalog.sqlite3"
    connection = sqlite3.connect(catalog)
    connection.execute("CREATE TABLE backups (id TEXT PRIMARY KEY, path TEXT)")
    connection.execute("INSERT INTO backups VALUES ('id', ?)", (str(source),))
    connection.commit()
    connection.close()

    def interrupt(stage: str) -> None:
        if stage == "rewrite":
            raise RuntimeError("controlled interruption")

    with pytest.raises(RuntimeError, match="controlled interruption"):
        migrate_storage(home=home, legacy=legacy, after_stage=interrupt)
    migrate_storage(home=home, legacy=legacy)
    assert not catalog.exists()


@pytest.mark.parametrize("location", ["data", "nested"])
def test_symlinked_legacy_storage_is_rejected_without_touching_target(
    tmp_path: Path, location: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy = _legacy_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    if location == "data":
        legacy["data"].rmdir()
        legacy["data"].symlink_to(outside, target_is_directory=True)
    else:
        (legacy["data"] / "environments").symlink_to(outside, target_is_directory=True)

    with pytest.raises(StorageMigrationError, match="symlink"):
        migrate_storage(home=home, legacy=legacy)

    assert sentinel.read_text(encoding="utf-8") == "keep"
