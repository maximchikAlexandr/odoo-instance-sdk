from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.postgres_size import database_size_bytes
from odoo_instance_sdk.models import (
    DatabaseFootprint,
    PythonEnvFootprint,
    StorageFootprint,
)

_CACHE: dict[str, tuple[float, StorageFootprint]] = {}
_CACHE_TTL = 15.0
_DU_TIMEOUT = 10.0


def _du_size(du: str, path: Path) -> int | None:
    """Try ``du -sb``; return bytes or ``None`` on any failure/timeout."""
    try:
        proc = subprocess.run(
            [du, "-sb", str(path)],
            capture_output=True,
            text=True,
            timeout=_DU_TIMEOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    token = proc.stdout.strip().split()
    if not token:
        return None
    try:
        return int(token[0])
    except ValueError:
        return None


def _walk_size(path: Path) -> int | None:
    """``os.walk`` with realpath dedup, symlinks skipped. ``None`` on OSError."""
    seen: set[Path] = set()
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
            for name in filenames:
                full = Path(dirpath, name)
                if os.path.islink(full):
                    continue
                try:
                    resolved = full.resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    total += full.stat().st_size
                except OSError:
                    return None
    except OSError:
        return None
    return total


def _directory_size(path: Path) -> int | None:
    """Best-effort directory size in bytes.

    Prefer ``du -sb`` (one syscall, handles hardlinks); fall back to ``os.walk`` with
    realpath dedup when ``du`` is missing, fails, or times out. Symlinks are never
    followed (``followlinks=False``) and symlink files themselves are skipped. Returns
    ``None`` if the path is not a directory or the walk raises ``OSError``.
    """
    if not path.is_dir():
        return None
    du = shutil.which("du")
    if du is not None:
        du_result = _du_size(du, path)
        if du_result is not None:
            return du_result
    return _walk_size(path)


def _other_files_bytes(generated_config_path: Path, dependency_lock_path: Path) -> int:
    """Sum sizes of generated config and lock files. Missing files contribute 0.

    ponytail: local logs/cache/artifacts live inside the worktree and are already
    counted by ``worktree_bytes``; scanning them separately would double-count.
    """
    total = 0
    for p in (generated_config_path, dependency_lock_path):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def collect_storage_footprint(
    *,
    environment_id: str,
    worktree_path: Path,
    python_environment_path: Path,
    python_environment_owned: bool,
    db_mode: str,
    generated_config_path: Path,
    dependency_lock_path: Path,
    target_db_name: str | None,
    db_host: str | None,
    db_port: int | None,
    db_user: str | None,
    db_password: str | None,
    data_dir: Path | None,
) -> StorageFootprint:
    """Collect a bounded-cached ``StorageFootprint`` for one environment.

    Cache is keyed by ``environment_id`` with a 15s TTL; concurrent callers within
    that window get the same result. ``complete=False`` flags any owned component
    (worktree, owned venv, owned DB) that could not be measured.
    """
    now = time.monotonic()
    cached = _CACHE.get(environment_id)
    if cached is not None and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    complete_flags: list[bool] = []

    worktree_bytes = _directory_size(worktree_path)
    complete_flags.append(worktree_bytes is not None)

    if python_environment_owned:
        py_bytes = _directory_size(python_environment_path)
        py = PythonEnvFootprint(owned=True, bytes=py_bytes)
        complete_flags.append(py_bytes is not None)
    else:
        py = PythonEnvFootprint(owned=False, bytes=None)

    if db_mode == "copy":
        pg_bytes: int | None = None
        if target_db_name is not None:
            pg_bytes = database_size_bytes(
                host=db_host,
                port=db_port if db_port is not None else 5432,
                user=db_user,
                password=db_password,
                database_name=target_db_name,
            )
        fs_bytes: int | None = None
        if data_dir is not None and target_db_name is not None:
            try:
                fs_path = validate_filestore_containment(data_dir, target_db_name)
            except ConfigError:
                fs_path = None
            if fs_path is not None:
                fs_bytes = _directory_size(fs_path)
        db_total: int | None
        if pg_bytes is None and fs_bytes is None:
            db_total = None
        else:
            db_total = (pg_bytes or 0) + (fs_bytes or 0)
        db = DatabaseFootprint(
            owned=True,
            postgres_bytes=pg_bytes,
            filestore_bytes=fs_bytes,
            total_bytes=db_total,
        )
        if pg_bytes is not None and fs_bytes is not None:
            complete_flags.append(True)
        else:
            complete_flags.append(False)
    else:
        db = DatabaseFootprint(
            owned=False, postgres_bytes=None, filestore_bytes=None, total_bytes=None
        )

    other = _other_files_bytes(generated_config_path, dependency_lock_path)

    total = (worktree_bytes or 0) + (py.bytes or 0) + (db.total_bytes or 0) + other
    footprint = StorageFootprint(
        total_bytes=total,
        complete=all(complete_flags),
        worktree_bytes=worktree_bytes,
        python_environment=py,
        database=db,
        other_files_bytes=other,
    )
    _CACHE[environment_id] = (time.monotonic(), footprint)
    return footprint


def clear_storage_footprint_cache(environment_id: str | None = None) -> None:
    """Drop cached footprint. ``None`` clears all entries (test helper)."""
    if environment_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(environment_id, None)
