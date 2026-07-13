"""Platform-specific cache directory resolution (stdlib only, no platformdirs)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_backup_dir() -> Path:
    """Return the platform-appropriate default backup directory.

    POSIX: $XDG_CACHE_HOME/odoo-instance-sdk/backups/ or ~/.cache/.../backups/
    Windows: %LOCALAPPDATA%/odoo-instance-sdk/backups/ or ~/AppData/Local/.../backups/
    """
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data is not None else Path.home() / "AppData" / "Local"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache is not None else Path.home() / ".cache"

    return base / "odoo-instance-sdk" / "backups"
