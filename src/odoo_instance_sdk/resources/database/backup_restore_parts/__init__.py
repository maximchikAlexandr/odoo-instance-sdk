from __future__ import annotations

# ruff: noqa: F401
import contextlib
import hashlib
import os
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

import httpx

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    BackupDownloadError,
    BackupNotAvailableError,
    ConfigError,
    DatabaseAlreadyExistsError,
    DatabaseError,
    DatabaseManagerUnavailableError,
    DropFailedError,
    InstanceConfigurationError,
    MasterPasswordRequiredError,
    PostgresClusterNotOwnedError,
    RestoreFailedError,
)
from odoo_instance_sdk.internal.files import (
    ensure_destination,
    extract_server_filename,
    make_download_filename,
)
from odoo_instance_sdk.internal.locks import backup_lock_path, exclusive_lock
from odoo_instance_sdk.internal.paths import get_backups_dir
from odoo_instance_sdk.internal.redact import format_error
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.urls import assert_local, warn_if_cleartext_secret
from odoo_instance_sdk.models import (
    AdminPasswordResetResult,
    Backup,
    BackupDownloadFailureContext,
    BackupFormat,
    BackupState,
    Database,
    DropResult,
    LocksResult,
    MonitoringInitializationResult,
    NoBackup,
    PostgresBloatResult,
    PostgresStatsResult,
    RestoreResult,
    SqlExecutionResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import (
        PreparedAction,
        PreparedStep,
        ProcessExecutor,
        ProcessResult,
        RunContext,
    )
    from odoo_instance_sdk.resources.instance import OdooInstance
from odoo_instance_sdk.resources.database.backup_restore_parts.backup import _BackupMixin
from odoo_instance_sdk.resources.database.backup_restore_parts.queries import _QueriesMixin


@dataclass(slots=True, kw_only=True)
class DatabaseResource(_QueriesMixin, _BackupMixin):
    base_url: str
    master_password: str | None = field(repr=False, default=None)
    _instance: OdooInstance = field(repr=False, hash=False, compare=False)
