from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.db_name import validate_filestore_containment
from odoo_instance_sdk.internal.postgres_size import database_size_bytes
from odoo_instance_sdk.internal.process_env import sanitized_child_environment
from odoo_instance_sdk.models import (
    DatabaseFootprint,
    PythonEnvFootprint,
    StorageFootprint,
)

_DU_TIMEOUT = 10.0
_SUBPROCESS_COMPAT = subprocess


@dataclass(frozen=True, slots=True)
class DatabaseStorageInput:
    """Database and filestore inputs belonging to one storage measurement."""

    mode: str
    target_name: str | None
    host: str | None
    port: int | None
    user: str | None
    password: str | None
    data_dir: Path | None


def _du_size(du: str, path: Path) -> int | None:
    """Try ``du -sb``; return bytes or ``None`` on any failure/timeout."""
    from odoo_instance_sdk.internal.proc import ProcessExecutionError, run_captured

    try:
        proc = run_captured(
            (du, "-sb", str(path)),
            env=sanitized_child_environment(),
            timeout=_DU_TIMEOUT,
            text=True,
        )
    except (ProcessExecutionError, OSError):
        return None
    if proc.returncode != 0:
        return None
    stdout = proc.stdout if isinstance(proc.stdout, str) else ""
    token = stdout.strip().split()
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


def _other_files_bytes(
    generated_config_path: Path, dependency_lock_path: Path, environment_root: Path | None
) -> int | None:
    """Measure environment-owned config, logs and artifact directories once."""
    total = 0
    paths = [generated_config_path, dependency_lock_path]
    if environment_root is not None:
        paths.extend(environment_root / name for name in ("odoo.log", "cache", "artifacts"))
    for p in paths:
        try:
            if p.is_file():
                total += p.stat().st_size
            elif p.is_dir():
                size = _directory_size(p)
                if size is None:
                    return None
                total += size
        except OSError:
            return None
    return total


def collect_storage_footprint(
    *,
    worktree_path: Path,
    python_environment_path: Path,
    python_environment_owned: bool,
    database: DatabaseStorageInput,
    generated_config_path: Path,
    dependency_lock_path: Path,
    environment_root: Path | None = None,
) -> StorageFootprint:
    """Pure compute (no cache): bounded ``StorageFootprint`` for one environment.

    ``complete=False`` flags any owned component (worktree, owned venv, owned DB)
    that could not be measured. It is stateless; the monitor owns cache lifetime.
    """
    complete_flags: list[bool] = []

    worktree_bytes = _directory_size(worktree_path)
    complete_flags.append(worktree_bytes is not None)

    if python_environment_owned:
        py_bytes = _directory_size(python_environment_path)
        py = PythonEnvFootprint(owned=True, bytes=py_bytes)
        complete_flags.append(py_bytes is not None)
    else:
        py = PythonEnvFootprint(owned=False, bytes=None)

    if database.mode == "copy":
        pg_bytes: int | None = None
        if database.target_name is not None:
            pg_bytes = database_size_bytes(
                host=database.host,
                port=database.port if database.port is not None else 5432,
                user=database.user,
                password=database.password,
                database_name=database.target_name,
            )
        fs_bytes: int | None = None
        if database.data_dir is not None and database.target_name is not None:
            try:
                fs_path = validate_filestore_containment(database.data_dir, database.target_name)
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

    other = _other_files_bytes(generated_config_path, dependency_lock_path, environment_root)
    if other is None:
        complete_flags.append(False)

    total = (worktree_bytes or 0) + (py.bytes or 0) + (db.total_bytes or 0) + (other or 0)
    return StorageFootprint(
        total_bytes=total,
        complete=all(complete_flags),
        worktree_bytes=worktree_bytes,
        python_environment=py,
        database=db,
        other_files_bytes=other,
    )
