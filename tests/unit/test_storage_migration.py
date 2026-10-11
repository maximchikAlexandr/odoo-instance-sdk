from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.storage_migration import (
    StorageMigrationConflictError,
    StorageMigrationError,
    ensure_storage_migrated,
    migrate_storage,
)


def _legacy_tree(tmp_path: Path) -> dict[str, Path]:
    roots = {name: tmp_path / f"legacy-{name}" for name in ("config", "data", "cache", "state")}
    for root in roots.values():
        root.mkdir()
    return roots


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


_PATH_ACCESS_METHODS = (
    "chmod",
    "exists",
    "is_dir",
    "is_file",
    "is_symlink",
    "mkdir",
    "open",
    "read_bytes",
    "read_text",
    "resolve",
    "rglob",
    "stat",
    "write_text",
)


@contextmanager
def _forbid_path_access(
    monkeypatch: pytest.MonkeyPatch, forbidden: tuple[Path, ...]
) -> Iterator[list[Path]]:
    touched: list[Path] = []
    with monkeypatch.context() as spy:
        for method_name in _PATH_ACCESS_METHODS:
            original = getattr(Path, method_name)

            def guarded(
                path: Path,
                *args: object,
                _original: object = original,
                _method_name: str = method_name,
                **kwargs: object,
            ) -> object:
                candidate = Path(os.fspath(path)).absolute()
                if any(candidate == root or candidate.is_relative_to(root) for root in forbidden):
                    touched.append(candidate)
                    raise AssertionError(
                        f"alternate startup crossed root via {_method_name}: {candidate}"
                    )
                return _original(path, *args, **kwargs)  # type: ignore[operator]

            spy.setattr(Path, method_name, guarded)
        yield touched


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


def test_alternate_startup_skips_legacy_discovery_and_keeps_roots_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    alternate = tmp_path / "alternate"
    legacy = _legacy_tree(tmp_path)
    sentinel = legacy["data"] / "sentinel"
    sentinel.write_text("legacy", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ODCLI_USER_ROOT", str(alternate))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.storage_migration.legacy_storage_roots",
        lambda *_args, **_kwargs: pytest.fail("alternate startup inspected legacy roots"),
    )

    result = ensure_storage_migrated()

    assert result.state == "complete"
    assert alternate.is_dir()
    assert not (home / ".odcli").exists()
    assert sentinel.read_text(encoding="utf-8") == "legacy"
    assert not (alternate / "storage-migration.json").exists()


def test_alternate_environment_path_check_does_not_inspect_legacy_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ODCLI_USER_ROOT", str(tmp_path / "alternate"))
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.storage_migration.legacy_storage_roots",
        lambda *_args, **_kwargs: pytest.fail("alternate path check inspected legacy roots"),
    )

    from odoo_instance_sdk.internal.paths import is_legacy_environment_path

    assert not is_legacy_environment_path("/legacy/odoo-instance-sdk/environments/example")


def test_alternate_startup_spy_never_reads_canonical_legacy_or_neighbor_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    selected = home / ".odcli-2"
    canonical = home / ".odcli"
    legacy = _legacy_tree(tmp_path)
    neighbors = (
        home / ".odcli-1",
        home / ".odcli-fix-42",
        home / ".local" / "share" / "odcli-slots" / "1",
        home / ".local" / "share" / "odcli-fix-tools" / "42",
    )
    forbidden = (canonical, *legacy.values(), *neighbors)
    for index, root in enumerate(forbidden):
        (root / "sentinel.txt").parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        (root / "sentinel.txt").write_text(f"sentinel-{index}", encoding="utf-8")
    before = {root: _tree_digest(root) for root in forbidden}

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ODCLI_USER_ROOT", str(selected))
    monkeypatch.setenv("ODCLI_LAUNCHER_KIND", "numbered")
    monkeypatch.setenv("ODCLI_LAUNCHER_ID", "2")

    with _forbid_path_access(monkeypatch, forbidden) as touched:
        from odoo_instance_sdk.internal.paths import is_legacy_environment_path
        from odoo_instance_sdk.internal.storage_migration import ensure_storage_migrated

        assert ensure_storage_migrated().state == "complete"
        assert not is_legacy_environment_path(str(legacy["data"] / "environments" / "old"))
    assert not touched

    for root, digest in before.items():
        assert _tree_digest(root) == digest
    assert selected.is_dir()
    assert not (selected / "storage-migration.json").exists()


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
