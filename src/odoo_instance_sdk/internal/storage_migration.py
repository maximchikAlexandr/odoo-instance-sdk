from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.locks import exclusive_lock_until

_JOURNAL_VERSION = 1
_APP_NAME = "odoo-instance-sdk"
_STAGES = ("inventory", "copy", "rewrite", "cleanup", "complete")


class StorageMigrationError(BackupCatalogError):
    """A global storage migration cannot safely complete."""


class StorageMigrationConflictError(StorageMigrationError):
    """Legacy and canonical storage contain incompatible paths."""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths
        joined = ", ".join(str(path) for path in paths)
        super().__init__(f"storage migration conflicts at: {joined}")


@dataclass(frozen=True, slots=True)
class StorageMigrationResult:
    """Durable result of one idempotent user-storage migration."""

    state: str
    migrated: tuple[str, ...] = ()
    conflicts: tuple[Path, ...] = ()


def _canonical_root(home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser().resolve() / ".odcli"


def legacy_storage_roots(home: Path | None = None) -> Mapping[str, Path]:
    """Return the POSIX platformdirs locations used before the unified root.

    XDG values are intentionally honored even when they point outside HOME;
    migration must never assume that a legacy path is home-relative.
    """
    if home is None:
        # This is discovery of the old layout only.  The active path provider
        # never calls platformdirs, so new artifacts cannot reappear there.
        import platformdirs

        return {
            "config": Path(platformdirs.user_config_dir(_APP_NAME, ensure_exists=False)),
            "data": Path(platformdirs.user_data_dir(_APP_NAME, ensure_exists=False)),
            "cache": Path(platformdirs.user_cache_dir(_APP_NAME, ensure_exists=False)),
            "state": Path(platformdirs.user_state_dir(_APP_NAME, ensure_exists=False)),
        }

    home_path = home.expanduser().resolve()

    def xdg(name: str, fallback: Path) -> Path:
        value = os.environ.get(name)
        return Path(value).expanduser() if value else fallback

    return {
        "config": xdg("XDG_CONFIG_HOME", home_path / ".config") / _APP_NAME,
        "data": xdg("XDG_DATA_HOME", home_path / ".local" / "share") / _APP_NAME,
        "cache": xdg("XDG_CACHE_HOME", home_path / ".cache") / _APP_NAME,
        "state": xdg("XDG_STATE_HOME", home_path / ".local" / "state") / _APP_NAME,
    }


def _journal_path(root: Path) -> Path:
    return root / "storage-migration.json"


def _lock_path(root: Path) -> Path:
    return root / ".storage-migration.lock"


def _lexical_path(path: Path) -> Path:
    """Return an absolute path without resolving symlinks."""
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _assert_no_symlink_components(path: Path) -> None:
    """Reject symlink roots/components before migration filesystem work."""
    lexical = _lexical_path(path)
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise StorageMigrationError(f"cannot inspect legacy storage path: {current}") from exc
        if stat.S_ISLNK(mode):
            raise StorageMigrationError(f"legacy storage path contains a symlink: {current}")


def _assert_safe_tree(path: Path) -> None:
    _assert_no_symlink_components(path)
    if not path.is_dir():
        return
    for child in path.iterdir():
        if child.is_symlink():
            raise StorageMigrationError(f"legacy storage path contains a symlink: {child}")
        if child.is_dir():
            _assert_safe_tree(child)


def _read_journal(path: Path) -> dict[str, JsonValue]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": _JOURNAL_VERSION, "stage": "inventory", "completed": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageMigrationError(f"invalid storage migration journal: {path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != _JOURNAL_VERSION:
        raise StorageMigrationError(f"unsupported storage migration journal: {path}")
    return payload


def _write_journal(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"symlink:")
        digest.update(os.readlink(path).encode())
    elif path.is_file():
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    elif path.is_dir():
        digest.update(b"directory")
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            digest.update(child.name.encode())
            digest.update(_digest(child).encode())
    else:
        digest.update(b"missing")
    return digest.hexdigest()


def _conflicts(source: Path, destination: Path) -> list[Path]:
    if not destination.exists() and not destination.is_symlink():
        return []
    if (
        source.is_dir()
        and destination.is_dir()
        and not source.is_symlink()
        and not destination.is_symlink()
    ):
        result: list[Path] = []
        for child in source.iterdir():
            result.extend(_conflicts(child, destination / child.name))
        return result
    return [] if _digest(source) == _digest(destination) else [destination]


def _copy_verified(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        if (
            source.is_dir()
            and destination.is_dir()
            and not source.is_symlink()
            and not destination.is_symlink()
        ):
            for child in source.iterdir():
                _copy_verified(child, destination / child.name)
            with contextlib.suppress(OSError):
                shutil.copystat(source, destination, follow_symlinks=False)
            if _digest(source) != _digest(destination):
                raise StorageMigrationError(
                    f"storage migration verification failed for {destination}"
                )
            return
        if _digest(source) != _digest(destination):
            raise StorageMigrationConflictError((destination,))
        return
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, destination, copy_function=shutil.copy2)
        with contextlib.suppress(OSError):
            shutil.copystat(source, destination, follow_symlinks=False)
    elif source.is_symlink():
        destination.symlink_to(os.readlink(source))
    else:
        shutil.copy2(source, destination)
    if _digest(source) != _digest(destination):
        raise StorageMigrationError(f"storage migration verification failed for {destination}")


def _path_rewrite(value: str | None, replacements: tuple[tuple[Path, Path], ...]) -> str | None:
    if value is None:
        return None
    path = Path(value)
    for source, destination in replacements:
        try:
            relative = path.resolve(strict=False).relative_to(source.resolve())
        except ValueError:
            continue
        return str(destination / relative)
    return value


def _rewrite_table_paths(
    connection: sqlite3.Connection,
    table: str,
    identity_column: str,
    path_columns: tuple[str, ...],
    replacements: tuple[tuple[Path, Path], ...],
) -> None:
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    for column in path_columns:
        if column not in columns:
            continue
        rows = connection.execute(
            f"SELECT {identity_column}, {column} FROM {table} WHERE {column} IS NOT NULL"
        ).fetchall()
        for identity, value in rows:
            rewritten = _path_rewrite(str(value), replacements)
            if rewritten != value:
                connection.execute(
                    f"UPDATE {table} SET {column}=? WHERE {identity_column}=?",
                    (rewritten, identity),
                )


def _rewrite_catalogue(path: Path, replacements: tuple[tuple[Path, Path], ...]) -> None:
    if not path.is_file():
        return
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("BEGIN")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "backups" in tables:
            _rewrite_table_paths(connection, "backups", "id", ("path",), replacements)
        if "environments" in tables:
            _rewrite_table_paths(
                connection,
                "environments",
                "id",
                (
                    "worktree_path",
                    "generated_config_path",
                    "python_environment_path",
                    "dependency_lock_path",
                ),
                replacements,
            )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise StorageMigrationError(f"could not rewrite catalogue paths: {path}") from exc
    finally:
        connection.close()


def _remove_verified(source: Path, destination: Path, original_digest: str) -> None:
    # Revalidate the lexical source immediately before cleanup.  Never turn a
    # changed legacy path into a resolved target owned by another directory.
    _assert_safe_tree(source)
    if not source.exists() and not source.is_symlink():
        return
    if not destination.exists() and not destination.is_symlink():
        raise StorageMigrationError(f"refusing to remove unverified source: {source}")
    if _digest(source) != original_digest:
        raise StorageMigrationError(f"refusing to remove changed source: {source}")
    if source.name == "catalog.sqlite3":
        # Path rewriting intentionally changes the copied catalogue bytes.  An
        # integrity check is the verification witness for this one file.
        connection = sqlite3.connect(str(destination))
        try:
            valid = connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            connection.close()
        if not valid:
            raise StorageMigrationError(f"refusing to remove corrupt catalogue source: {source}")
    elif _digest(source) != _digest(destination):
        raise StorageMigrationError(f"refusing to remove changed source: {source}")
    if source.is_dir() and not source.is_symlink():
        shutil.rmtree(source)
    else:
        source.unlink()


def _catalogue_is_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    connection = sqlite3.connect(str(path))
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        return isinstance(result, str) and result == "ok"
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def migrate_storage(  # noqa: C901
    *,
    home: Path | None = None,
    legacy: Mapping[str, Path] | None = None,
    after_stage: Callable[[str], None] | None = None,
) -> StorageMigrationResult:
    """Migrate legacy global state under one durable lock and journal.

    The callback exists for focused interruption tests and is deliberately
    called only after each durable stage marker has been written.
    """
    root = _canonical_root(home)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    journal_path = _journal_path(root)
    lock_path = _lock_path(root)
    with exclusive_lock_until(lock_path, deadline=_monotonic_deadline(30.0)):
        journal = _read_journal(journal_path)
        if journal.get("stage") == "complete":
            return StorageMigrationResult("complete")
        legacy_roots = {
            key: _lexical_path(Path(value))
            for key, value in (legacy or legacy_storage_roots(home)).items()
        }
        for legacy_root in legacy_roots.values():
            if legacy_root.exists() or legacy_root.is_symlink():
                _assert_safe_tree(legacy_root)
        mappings = (
            (legacy_roots["config"], root / "config"),
            (legacy_roots["data"] / "catalog.sqlite3", root / "catalog.sqlite3"),
            (legacy_roots["data"] / "environments", root / "environments"),
            (legacy_roots["data"] / "projects", root / "projects"),
            (legacy_roots["cache"] / "backups", root / "backups"),
            (legacy_roots["state"] / "locks", root / "locks"),
            (legacy_roots["data"] / "pgadmin", root / "pgadmin"),
        )
        existing = tuple(
            (source, destination)
            for source, destination in mappings
            if source.exists() or source.is_symlink()
        )
        for source, _ in existing:
            _assert_safe_tree(source)
        journal_stage = str(journal.get("stage", "inventory"))
        conflicts = tuple(
            path
            for source, destination in existing
            if not (
                source.name == "catalog.sqlite3"
                and journal_stage in {"rewrite", "cleanup"}
                and _catalogue_is_valid(destination)
            )
            for path in _conflicts(source, destination)
        )
        if conflicts:
            _write_journal(
                journal_path,
                {**journal, "stage": "inventory", "conflicts": [str(path) for path in conflicts]},
            )
            raise StorageMigrationConflictError(conflicts)

        _write_journal(
            journal_path,
            {**journal, "stage": "inventory", "sources": [str(source) for source, _ in existing]},
        )
        _call_stage(after_stage, "inventory")
        original_digests = {source: _digest(source) for source, _ in existing}
        for source, destination in existing:
            if not (
                source.name == "catalog.sqlite3"
                and journal_stage in {"rewrite", "cleanup"}
                and _catalogue_is_valid(destination)
            ):
                _copy_verified(source, destination)
        _write_journal(journal_path, {**journal, "stage": "copy"})
        _call_stage(after_stage, "copy")

        replacements = (
            (legacy_roots["data"] / "environments", root / "environments"),
            (legacy_roots["data"] / "projects", root / "projects"),
            (legacy_roots["cache"] / "backups", root / "backups"),
            (legacy_roots["state"] / "locks", root / "locks"),
            (legacy_roots["data"] / "pgadmin", root / "pgadmin"),
        )
        _rewrite_catalogue(root / "catalog.sqlite3", replacements)
        _write_journal(journal_path, {**journal, "stage": "rewrite"})
        _call_stage(after_stage, "rewrite")
        for source, destination in existing:
            _remove_verified(source, destination, original_digests[source])
        for legacy_root in legacy_roots.values():
            with contextlib.suppress(OSError):
                legacy_root.rmdir()
        _write_journal(journal_path, {**journal, "stage": "cleanup"})
        _call_stage(after_stage, "cleanup")
        for directory in ("config", "environments", "projects", "backups", "locks", "pgadmin"):
            target = root / directory
            target.mkdir(mode=0o700, exist_ok=True)
        _write_journal(journal_path, {**journal, "stage": "complete", "completed": list(_STAGES)})
        return StorageMigrationResult(
            "complete", tuple(str(destination) for _, destination in existing)
        )


def _monotonic_deadline(timeout: float) -> float:
    import time

    return time.monotonic() + timeout


def _call_stage(callback: Callable[[str], None] | None, stage: str) -> None:
    if callback is not None:
        callback(stage)


def ensure_storage_migrated() -> StorageMigrationResult:
    """Run the process-wide migration entry point used by path providers."""
    return migrate_storage()


run_storage_migration = migrate_storage
