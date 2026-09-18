from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from odoo_instance_sdk.storage.catalog.backup import _BackupMixin
from odoo_instance_sdk.storage.catalog.environment import _EnvironmentMixin
from odoo_instance_sdk.storage.catalog.helpers import *  # noqa: F403


@dataclass(slots=True, kw_only=True)
class BackupCatalog(_BackupMixin, _EnvironmentMixin):
    db_path: Path
    _conn: sqlite3.Connection = field(init=False, repr=False)
    _read_only: bool = field(init=False, default=False, repr=False)
