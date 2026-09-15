from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

import msgspec


class BackupFormat(enum.StrEnum):
    ZIP = "zip"
    DUMP = "dump"


class PostgresClusterState(enum.StrEnum):
    """Lifecycle state of a project-level PostgreSQL cluster.

    UNKNOWN    — not probed (initial).
    UNREACHABLE — endpoint not reachable.
    STARTING   — compose up issued, not yet healthy.
    HEALTHY    — ready.
    STOPPED    — compose stopped.
    UNHEALTHY  — running but healthcheck failing.
    """

    UNKNOWN = "unknown"
    UNREACHABLE = "unreachable"
    STARTING = "starting"
    HEALTHY = "healthy"
    STOPPED = "stopped"
    UNHEALTHY = "unhealthy"


class BackupState(enum.StrEnum):
    DOWNLOADING = "downloading"
    AVAILABLE = "available"
    FAILED = "failed"
    DELETED = "deleted"


class BackupEventType(enum.StrEnum):
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_SUCCEEDED = "download_succeeded"
    DOWNLOAD_FAILED = "download_failed"
    VALIDATION_SUCCEEDED = "validation_succeeded"
    VALIDATION_FAILED = "validation_failed"
    VALIDATION_UNAVAILABLE = "validation_unavailable"
    DELETED = "deleted"


class BackupValidationStatus(enum.StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class EnvironmentState(enum.StrEnum):
    """Persisted lifecycle state shared by catalog and monitor contracts."""

    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"
    REMOVING = "removing"
    CLEANUP_FAILED = "cleanup_failed"
    REMOVED = "removed"


class EnvironmentDatabaseMode(enum.StrEnum):
    SHARED = "shared"
    COPY = "copy"


class EnvironmentPythonMode(enum.StrEnum):
    CREATE = "create"
    REUSE = "reuse"


class BackupBranchOrigin(enum.StrEnum):
    EXPLICIT = "explicit"
    CONFIGURED = "configured"
    UNKNOWN = "unknown"


class BackupProvenanceStatus(enum.StrEnum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class BackupFreshness(enum.StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class DatabasePreparationAction(enum.StrEnum):
    DOWNLOAD = "download"
    RESTORE = "restore"
    RESET_ADMIN_PASSWORD = "reset_admin_password"
    SWITCH_DEFAULT = "switch_default"


class Backup(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A successfully downloaded backup.

    Convention: ``downloaded_at`` is timezone-aware (UTC). ``NoBackup`` also
    uses a tz-aware default. Downstream code that reads
    ``db.backup.downloaded_at`` on a ``Backup | NoBackup`` union can rely on
    the value being tz-aware.
    """

    id: uuid.UUID
    source_base_url: str
    database_name: str
    format: BackupFormat
    filestore_requested: bool
    path: str
    filename: str
    size_bytes: int
    sha256: str
    downloaded_at: datetime
    source_git_branch: str | None = None


class BackupDownloadFailureContext(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Secret-free identity and catalogue state retained after a download failure."""

    backup_id: uuid.UUID
    state: BackupState
    published: bool


class NoBackup(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    id: uuid.UUID = uuid.UUID(int=0)
    source_base_url: str = ""
    database_name: str = ""
    format: BackupFormat | None = None
    filestore_requested: bool = False
    path: str = ""
    filename: str = ""
    size_bytes: int = 0
    sha256: str = ""
    downloaded_at: datetime = datetime.fromtimestamp(0, UTC)
    source_git_branch: str | None = None


class DevelopmentEnvironment(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: uuid.UUID
    name: str
    repository_root: str
    git_common_dir: str
    branch: str
    base_ref: str
    worktree_path: str
    generated_config_path: str
    python_environment_path: str
    python_environment_owned: bool
    dependency_lock_path: str
    http_interface: str
    http_port: int
    db_mode: EnvironmentDatabaseMode
    source_db_name: str | None = None
    target_db_name: str | None = None
    backup_id: uuid.UUID | None = None
    state: EnvironmentState
    created_at: datetime
    last_used_at: datetime | None = None
    removed_at: datetime | None = None
    last_error: str | None = None


class BackupProvenanceComparison(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    status: BackupProvenanceStatus
    expected_base_ref: str
    recorded_branch: str | None


class DatabaseRefreshOptions(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    restore: bool = False
    source_branch: str | None = None
    reset_admin_password: bool = False


class DatabasePreparationResult(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    mode: DatabasePreparationAction
    backup: Backup | None = None
    source_git_branch: str | None = None
    branch_origin: BackupBranchOrigin = BackupBranchOrigin.UNKNOWN
    restored_database: str | None = None
    admin_password_reset: bool = False
    default_switched: bool = False
    previous_default: str | None = None
    effective_default: str | None = None
    retained_artifacts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class AdminPasswordResetResult(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    database: str
    completed: bool
    xml_id: str
    environment_id: uuid.UUID | None = None


class EnvironmentCheckoutPlan(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    name: str
    branch: str
    effective_base_ref: str
    db_mode: EnvironmentDatabaseMode
    source_database: str | None
    target_database: str | None
    python_mode: EnvironmentPythonMode
    provenance: BackupProvenanceComparison
    freshness: BackupFreshness
    preparation_actions: tuple[DatabasePreparationAction, ...]
    warnings: tuple[str, ...]


class EnvironmentCheckoutResult(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    environment: DevelopmentEnvironment
    plan: EnvironmentCheckoutPlan


class Database(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    name: str
    backup: Backup | NoBackup


class BackupEvent(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    backup_id: uuid.UUID
    sequence: int
    event_type: BackupEventType
    occurred_at: datetime
    path: str | None = None
    validator: str | None = None
    exit_code: int | None = None
    message: str | None = None


class BackupValidationResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    valid: bool
    errors: tuple[str, ...] = ()
    db_name: str | None = None
    db_version: str | None = None


class BackupDeletionResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    file_existed: bool
    already_deleted: bool
    deleted_at: datetime
