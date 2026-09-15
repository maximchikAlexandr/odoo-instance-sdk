from __future__ import annotations

# ruff: noqa: F401
import base64
import binascii
import functools
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, ParamSpec, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    BackupNotAvailableError,
    BackupNotFoundError,
)
from odoo_instance_sdk.internal.applied_settings import (
    LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON,
    AppliedSettingsError,
    decode_applied_settings,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.sanitize import sanitize_event_message, sanitize_last_error
from odoo_instance_sdk.models import (
    Backup,
    BackupEvent,
    BackupEventType,
    BackupFormat,
    BackupState,
    BackupValidationStatus,
    EnvironmentState,
)
from odoo_instance_sdk.storage.catalog_migrate import (
    CATALOG_REVISION,
    ensure_catalog_migrated,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.storage.catalog import helpers as _helpers
from odoo_instance_sdk.storage.catalog.backup import _BackupMixin
from odoo_instance_sdk.storage.catalog.environment import _EnvironmentMixin
from odoo_instance_sdk.storage.catalog.helpers import *  # noqa: F403


@dataclass(slots=True, kw_only=True)
class BackupCatalog(_BackupMixin, _EnvironmentMixin):
    db_path: Path
    _conn: sqlite3.Connection = field(init=False, repr=False)
    _read_only: bool = field(init=False, default=False, repr=False)
