from __future__ import annotations

from pathlib import Path

import platformdirs

_APP_NAME = "odoo-instance-sdk"


def get_cache_root() -> Path:
    return Path(platformdirs.user_cache_dir(_APP_NAME, ensure_exists=True))


def get_backups_dir() -> Path:
    return get_cache_root() / "backups"


def get_data_root(*, ensure_exists: bool = True) -> Path:
    return Path(platformdirs.user_data_dir(_APP_NAME, ensure_exists=ensure_exists))


def get_state_root() -> Path:
    return Path(platformdirs.user_state_dir(_APP_NAME, ensure_exists=True))


def get_catalog_path() -> Path:
    return get_data_root() / "catalog.sqlite3"


def get_environments_root(*, ensure_exists: bool = True) -> Path:
    return get_data_root(ensure_exists=ensure_exists) / "environments"


def get_locks_dir() -> Path:
    return get_state_root() / "locks"


def get_project_postgres_dir(project_id: str) -> Path:
    """Runtime artifacts directory for a project's SDK-owned PostgreSQL cluster.

    ``project_id`` is expected to be a deterministic identifier (e.g. ``repo_key``).
    The directory is created lazily by callers — this function only returns the path.
    """
    return get_data_root(ensure_exists=False) / "projects" / project_id / "postgres"


def get_pgadmin_root(*, ensure_exists: bool = False) -> Path:
    """Return the user-global pgAdmin state root without creating it by default."""
    root = get_data_root(ensure_exists=ensure_exists) / "pgadmin"
    if ensure_exists:
        root.mkdir(mode=0o710, parents=True, exist_ok=True)
    return root


def get_pgadmin_private_dir(*, ensure_exists: bool = False) -> Path:
    root = get_pgadmin_root(ensure_exists=ensure_exists)
    private = root / "private"
    if ensure_exists:
        private.mkdir(mode=0o710, exist_ok=True)
    return private


def get_pgadmin_data_dir(*, ensure_exists: bool = False) -> Path:
    root = get_pgadmin_root(ensure_exists=ensure_exists)
    data = root / "data"
    if ensure_exists:
        data.mkdir(mode=0o770, exist_ok=True)
    return data
