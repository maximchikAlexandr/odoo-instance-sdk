from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from odoo_instance_sdk.exceptions import LockConflictError

_LOCK_MODES = {"exclusive": fcntl.LOCK_EX, "shared": fcntl.LOCK_SH}


@contextlib.contextmanager
def _acquire(path: Path, mode: int, mode_name: str) -> Iterator[int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError as e:
            os.close(fd)
            raise LockConflictError(str(path), mode=mode_name) from e
        yield fd
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


@contextlib.contextmanager
def exclusive_lock(path: Path) -> Iterator[int]:
    with _acquire(path, _LOCK_MODES["exclusive"], "exclusive") as fd:
        yield fd


@contextlib.contextmanager
def shared_lock(path: Path) -> Iterator[int]:
    with _acquire(path, _LOCK_MODES["shared"], "shared") as fd:
        yield fd


def catalog_migration_lock_path() -> Path:
    from odoo_instance_sdk.internal.paths import get_locks_dir

    return get_locks_dir() / "catalog-migration.lock"


def provisioning_lock_path(repo_key: str, branch: str) -> Path:
    from odoo_instance_sdk.internal.paths import get_locks_dir

    safe_branch = branch.replace(os.sep, "_").replace("/", "_")
    return get_locks_dir() / f"provision-{repo_key}-{safe_branch}.lock"


def environment_lock_path(environment_id: str) -> Path:
    from odoo_instance_sdk.internal.paths import get_locks_dir

    return get_locks_dir() / f"env-{environment_id}.lock"


def python_env_lock_path(python_env_path: str) -> Path:
    from odoo_instance_sdk.internal.paths import get_locks_dir

    digest = hashlib.sha256(python_env_path.encode("utf-8")).hexdigest()[:16]
    return get_locks_dir() / f"pyenv-{digest}.lock"
