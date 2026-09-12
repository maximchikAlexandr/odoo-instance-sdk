from __future__ import annotations

import os
from pathlib import Path

_USER_ROOT_NAME = ".odcli"


def _user_root(*, ensure_exists: bool = True) -> Path:
    """Return the one SDK-owned root without consulting platformdirs."""
    root = Path.home().expanduser().resolve() / _USER_ROOT_NAME
    if ensure_exists:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    return root


def get_user_root(*, ensure_exists: bool = True) -> Path:
    """Return the canonical global OdCli root (never repository-local ``.odcli``)."""
    return _user_root(ensure_exists=ensure_exists)


def get_config_root(*, ensure_exists: bool = True) -> Path:
    root = _user_root(ensure_exists=ensure_exists) / "config"
    if ensure_exists:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    return root


def get_cache_root(*, ensure_exists: bool = True) -> Path:
    """Compatibility name for the canonical global root.

    Backups now have their own child directory; no platform cache root is used.
    """
    return _user_root(ensure_exists=ensure_exists)


def get_backups_dir(*, ensure_exists: bool = True) -> Path:
    root = _user_root(ensure_exists=ensure_exists) / "backups"
    if ensure_exists:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    return root


def get_data_root(*, ensure_exists: bool = True) -> Path:
    return _user_root(ensure_exists=ensure_exists)


def get_state_root(*, ensure_exists: bool = True) -> Path:
    return _user_root(ensure_exists=ensure_exists)


def get_catalog_path(*, ensure_exists: bool = True) -> Path:
    return _user_root(ensure_exists=ensure_exists) / "catalog.sqlite3"


def get_environments_root(*, ensure_exists: bool = True) -> Path:
    root = _user_root(ensure_exists=ensure_exists) / "environments"
    if ensure_exists:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    return root


def get_locks_dir(*, ensure_exists: bool = True) -> Path:
    root = _user_root(ensure_exists=ensure_exists) / "locks"
    if ensure_exists:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    return root


def get_project_postgres_dir(project_id: str) -> Path:
    """Return a project runtime directory below the canonical user root."""
    return get_data_root(ensure_exists=False) / "projects" / project_id / "postgres"


def get_pgadmin_root(*, ensure_exists: bool = False) -> Path:
    """Return the user-global pgAdmin state root without creating it by default."""
    root = get_data_root(ensure_exists=ensure_exists) / "pgadmin"
    if ensure_exists:
        root.mkdir(mode=0o710, parents=True, exist_ok=True)
        os.chmod(root, 0o710)
    return root


def get_pgadmin_private_dir(*, ensure_exists: bool = False) -> Path:
    root = get_pgadmin_root(ensure_exists=ensure_exists)
    private = root / "private"
    if ensure_exists:
        private.mkdir(mode=0o710, exist_ok=True)
        os.chmod(private, 0o710)
    return private


def get_pgadmin_data_dir(*, ensure_exists: bool = False) -> Path:
    root = get_pgadmin_root(ensure_exists=ensure_exists)
    data = root / "data"
    if ensure_exists:
        data.mkdir(mode=0o770, exist_ok=True)
        os.chmod(data, 0o770)
    return data


def get_storage_migration_lock_path() -> Path:
    # Keep the coordinator lock beside, rather than inside, the directory it
    # migrates; a legacy locks directory must be comparable as a whole.
    return _user_root(ensure_exists=False) / ".storage-migration.lock"


def get_storage_migration_journal_path() -> Path:
    return _user_root(ensure_exists=False) / "storage-migration.json"
