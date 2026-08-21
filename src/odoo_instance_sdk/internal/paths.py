from __future__ import annotations

from pathlib import Path

import platformdirs

_APP_NAME = "odoo-instance-sdk"


def get_cache_root() -> Path:
    return Path(platformdirs.user_cache_dir(_APP_NAME, ensure_exists=True))


def get_backups_dir() -> Path:
    return get_cache_root() / "backups"


def get_data_root() -> Path:
    return Path(platformdirs.user_data_dir(_APP_NAME, ensure_exists=True))


def get_state_root() -> Path:
    return Path(platformdirs.user_state_dir(_APP_NAME, ensure_exists=True))


def get_catalog_path() -> Path:
    return get_data_root() / "catalog.sqlite3"


def get_environments_root() -> Path:
    return get_data_root() / "environments"


def get_locks_dir() -> Path:
    return get_state_root() / "locks"


def get_legacy_catalog_path() -> Path:
    return get_cache_root() / "backups.sqlite3"
