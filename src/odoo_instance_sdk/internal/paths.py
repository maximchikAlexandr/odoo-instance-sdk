from __future__ import annotations

from pathlib import Path

import platformdirs


def get_cache_root() -> Path:
    return Path(platformdirs.user_cache_dir("odoo-instance-sdk", ensure_exists=True))


def get_backups_dir() -> Path:
    return get_cache_root() / "backups"
