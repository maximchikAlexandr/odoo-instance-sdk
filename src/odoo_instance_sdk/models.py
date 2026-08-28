from __future__ import annotations

import enum
import uuid
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import msgspec

type ClusterUnavailabilityReason = Literal[
    "external_not_owned",
    "stopped",
    "missing",
    "docker_unavailable",
    "inspect_failed",
    "stats_failed",
]


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


class StartConfig(msgspec.Struct, forbid_unknown_fields=True):
    http_port: int = 8069
    http_interface: str = "127.0.0.1"
    config_path: str | None = None
    addons_path: list[str] | None = None
    data_dir: str | None = None
    dbfilter: str | None = None
    workers: int | None = None
    max_cron_threads: int | None = None
    log_level: Literal["debug", "info", "warning", "error", "critical", "notset"] | None = None
    log_handler: str | None = None
    logfile: str | None = None
    dev_mode: Literal["all"] | list[str] | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None
    load_language: str | None = None

    @classmethod
    def from_odoo_config(cls, path: str | Path) -> StartConfig:
        """Build a StartConfig by reading fields from an odoo.conf file.

        Literal fields (``log_level``, ``dev_mode``) are validated by msgspec
        at construction; an invalid value raises ``msgspec.ValidationError``.
        """
        # local import: odoo_config -> urls -> exceptions; none import models,
        # but keeping it lazy avoids any import-order surprise at package init.
        from odoo_instance_sdk.internal.odoo_config import parse_odoo_config

        cfg = parse_odoo_config(path)

        def _get(name: str) -> str | None:
            v = cfg.get(name)
            return v if v else None

        def _int(name: str) -> int | None:
            v = _get(name)
            if v is None:
                return None
            try:
                return int(v)
            except ValueError:
                warnings.warn(
                    f"Invalid int for {name} in odoo.conf: {v!r}; using default",
                    stacklevel=3,
                )
                return None

        def _list(name: str) -> list[str] | None:
            v = _get(name)
            if v is None:
                return None
            return [s.strip() for s in v.split(",") if s.strip()]

        def _dev_mode() -> Literal["all"] | list[str] | None:
            v = _get("dev_mode")
            if v is None:
                return None
            if "," in v:
                return [s.strip() for s in v.split(",") if s.strip()]
            return cast("Literal['all']", v)

        return cls(
            http_port=_int("http_port") or 8069,
            http_interface=_get("http_interface") or "127.0.0.1",
            config_path=str(Path(path)),
            addons_path=_list("addons_path"),
            data_dir=_get("data_dir"),
            dbfilter=_get("dbfilter"),
            workers=_int("workers"),
            max_cron_threads=_int("max_cron_threads"),
            log_level=cast(
                "Literal['debug', 'info', 'warning', 'error', 'critical', 'notset'] | None",
                _get("log_level"),
            ),
            log_handler=_get("log_handler"),
            logfile=_get("logfile"),
            dev_mode=_dev_mode(),
            db_host=_get("db_host"),
            db_port=_int("db_port"),
            db_user=_get("db_user"),
            db_password=_get("db_password"),
            db_name=_get("db_name"),
            load_language=_get("load_language"),
        )

    def __repr__(self) -> str:
        parts: list[str] = []
        for f in msgspec.structs.fields(self):
            val = getattr(self, f.name)
            if f.name == "db_password" and val is not None:
                parts.append(f"{f.name}=<redacted>")
            else:
                parts.append(f"{f.name}={val!r}")
        return f"StartConfig({', '.join(parts)})"


class CommandResult(msgspec.Struct):
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float


class OdooTestSpec(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The transport-neutral input contract for one native Odoo test run."""

    modules: tuple[str, ...]
    test_tags: str
    reload_tests: bool = False
    allow_empty: bool = False

    def __post_init__(self) -> None:
        if not self.modules:
            raise ValueError("OdooTestSpec.modules must not be empty")
        if tuple(sorted(set(self.modules))) != self.modules:
            raise ValueError("OdooTestSpec.modules must be sorted and unique")
        if not isinstance(self.test_tags, str) or not self.test_tags.strip():
            raise ValueError("OdooTestSpec.test_tags must not be blank")


class OdooTestResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The stable native report/result contract for one Odoo test run."""

    counts: dict[str, int]
    failures: bool
    zero_tests: bool
    exit_code: int

    def __post_init__(self) -> None:
        required = {"tests", "successful", "failed", "errors", "skipped"}
        if set(self.counts) != required:
            raise ValueError("OdooTestResult.counts must contain exactly five count keys")
        if any(type(value) is not int or value < 0 for value in self.counts.values()):
            raise ValueError("OdooTestResult.counts values must be non-negative integers")


class OdooProcess(msgspec.Struct):
    id: str
    pid: int
    args: list[str]
    started_at: float

    def __repr__(self) -> str:
        masked: list[str] = []
        for i, a in enumerate(self.args):
            if i > 0 and self.args[i - 1] == "--config":
                masked.append("<redacted>")
            else:
                masked.append(a)
        return f"OdooProcess(id={self.id!r}, pid={self.pid!r}, args={masked!r}, started_at={self.started_at!r})"


class ProcessStatus(msgspec.Struct):
    state: Literal["running", "exited"]
    returncode: int | None = None


class ReadinessResult(msgspec.Struct):
    ok: bool
    elapsed: float
    attempts: int
    final_status: str | None = None


class RestoreResult(msgspec.Struct):
    new_db: str
    source: Backup


class DropResult(msgspec.Struct):
    db: str


class RuntimeState(enum.StrEnum):
    STOPPED = "stopped"
    READY = "ready"
    NOT_READY = "not_ready"


class GitActivityState(enum.StrEnum):
    CLEAN = "clean"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    ORPHAN = "orphan"


class PidScope(enum.StrEnum):
    HOST = "host"
    DOCKER_VM = "docker_vm"
    UNAVAILABLE = "unavailable"


class PortObservation(enum.StrEnum):
    FREE = "free"
    OCCUPIED = "occupied"
    UNKNOWN = "unknown"


class GitDiff(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    added: int
    deleted: int


class GitActivity(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    default_branch: str
    head_sha: str | None
    short_sha: str | None
    branch: str
    ahead: int | None
    behind: int | None
    diff: GitDiff | None
    state: GitActivityState


class PythonEnvFootprint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    owned: bool
    bytes: int | None


class DatabaseFootprint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    owned: bool
    postgres_bytes: int | None
    filestore_bytes: int | None
    total_bytes: int | None


class StorageFootprint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    total_bytes: int
    complete: bool
    worktree_bytes: int | None
    python_environment: PythonEnvFootprint
    database: DatabaseFootprint
    other_files_bytes: int | None


class EnvironmentArtifacts(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    worktree_exists: bool
    worktree_registered: bool
    config_exists: bool
    python_exists: bool
    python_contained: bool
    dependency_lock_exists: bool
    backup_exists: bool | None


class RuntimeMetrics(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    state: RuntimeState
    root_pid: int | None
    child_pids: tuple[int, ...]
    process_count: int
    cpu_percent: float | None
    rss_bytes: int | None
    started_at: datetime | None
    http_url: str | None
    http_port: int | None
    database_name: str | None
    commit_sha: str | None
    branch: str | None


class ClusterContainer(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: str | None
    name: str | None
    image: str | None
    pid: int | None
    pid_scope: PidScope


class ClusterMetrics(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    cpu_percent: float | None
    memory_usage_bytes: int | None
    memory_limit_bytes: int | None
    volume_usage_bytes: int | None
    sampled_at: datetime | None


class ClusterEndpoint(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    host: str
    port: int


class ClusterResourceSnapshot(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True
):
    container: ClusterContainer | None
    metrics: ClusterMetrics | None
    unavailability_reason: ClusterUnavailabilityReason | None
    sampled_at: datetime | None


class ClusterSnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    mode: Literal["external", "compose"]
    owned: bool
    state: PostgresClusterState
    endpoint: ClusterEndpoint | None
    container: ClusterContainer | None
    metrics: ClusterMetrics | None
    unavailability_reason: ClusterUnavailabilityReason | None
    sampled_at: datetime | None


class EnvironmentSnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: str
    project_id: str
    name: str
    branch: str
    short_sha: str | None
    db_mode: Literal["shared", "copy"]
    database: str | None
    lifecycle_state: EnvironmentState
    allocated_http_port: int | None
    observed_port: PortObservation | None
    artifacts: EnvironmentArtifacts
    runtime: RuntimeMetrics
    git: GitActivity
    storage: StorageFootprint


class ProjectSummary(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    id: str
    name: str
    display_hint: str
    environment_count: int
    cluster: ClusterSnapshot | None


class Snapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    schema_version: int
    generated_at: datetime
    projects: tuple[ProjectSummary, ...]
    environments: tuple[EnvironmentSnapshot, ...]
