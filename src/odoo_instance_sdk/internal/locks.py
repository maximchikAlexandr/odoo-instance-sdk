from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import time
from collections.abc import Iterator
from pathlib import Path

from odoo_instance_sdk.exceptions import LockConflictError
from odoo_instance_sdk.internal.db_name import validate_db_name

_LOCK_MODES = {"exclusive": fcntl.LOCK_EX, "shared": fcntl.LOCK_SH}


def _release(fd: int) -> None:
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(fd)


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
        _release(fd)


@contextlib.contextmanager
def exclusive_lock(path: Path) -> Iterator[int]:
    with _acquire(path, _LOCK_MODES["exclusive"], "exclusive") as fd:
        yield fd


@contextlib.contextmanager
def exclusive_lock_until(path: Path, deadline: float) -> Iterator[int]:
    """Acquire exclusively, waiting only until the caller's monotonic deadline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockConflictError(str(path), mode="exclusive") from exc
                time.sleep(min(0.05, remaining))
        yield fd
    finally:
        _release(fd)


@contextlib.contextmanager
def shared_lock(path: Path) -> Iterator[int]:
    with _acquire(path, _LOCK_MODES["shared"], "shared") as fd:
        yield fd


def provisioning_lock_path() -> Path:
    from odoo_instance_sdk.internal.paths import get_locks_dir

    # Port allocation is catalog-global; a branch-scoped lock permits two
    # simultaneous checkouts to select the same port.
    return get_locks_dir() / "environment-port-allocation.lock"


def environment_lock_path(environment_id: str) -> Path:
    from odoo_instance_sdk.internal.paths import get_locks_dir

    return get_locks_dir() / f"env-{environment_id}.lock"


def postgres_cluster_lock_path(project_id: str) -> Path:
    """Serialize a managed cluster's status/reconcile/up lifecycle."""
    from odoo_instance_sdk.internal.paths import get_locks_dir

    return get_locks_dir() / f"postgres-{project_id}.lock"


def database_preparation_lock_path(project_id: str) -> Path:
    """Return the canonical project-wide database preparation lock."""
    from odoo_instance_sdk.internal.paths import get_locks_dir

    return get_locks_dir() / f"database-preparation-{project_id}.lock"


def database_preparation_artifact_lock_path(project_id: str, database_name: str) -> Path:
    """Return the exclusive lock for a project preparation target database."""
    from odoo_instance_sdk.internal.paths import get_locks_dir

    validate_db_name(database_name)
    return get_locks_dir() / f"database-preparation-{project_id}-{database_name}.lock"


def python_env_lock_path(python_env_path: str) -> Path:
    from odoo_instance_sdk.internal.paths import get_locks_dir

    digest = hashlib.sha256(python_env_path.encode("utf-8")).hexdigest()[:16]
    return get_locks_dir() / f"pyenv-{digest}.lock"


def pgadmin_lock_path() -> Path:
    """Return the one user-global lock for pgAdmin preparation and lifecycle."""
    from odoo_instance_sdk.internal.paths import get_locks_dir

    return get_locks_dir() / "pgadmin.lock"
