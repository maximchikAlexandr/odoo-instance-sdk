"""Shared read-only catalogue and identity helpers for resource projections."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from urllib.parse import quote
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def stable_resource_identity(kind: str, value: str) -> str:
    """Return a deterministic, non-path identity for local resource joins."""
    if not kind.strip() or not value.strip():
        raise ValueError("resource identity inputs must not be empty")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{digest}"


def open_catalog_read_only(path: Path) -> BackupCatalog | None:
    """Open an existing catalogue without schema creation or reconciliation."""
    if not path.is_file():
        return None
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog.__new__(BackupCatalog)
    catalog.db_path = path
    catalog._read_only = True
    catalog._conn = sqlite3.connect(
        f"file:{quote(str(path.resolve()), safe='/')}?mode=ro", uri=True
    )
    catalog._conn.row_factory = sqlite3.Row
    return catalog


__all__ = ["open_catalog_read_only", "stable_resource_identity"]
