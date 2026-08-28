from __future__ import annotations


class OdooInstanceSdkError(Exception):
    """Base exception for all SDK errors."""


class ConfigError(OdooInstanceSdkError):
    """Invalid configuration."""  # ponytail: spec-mandated, not yet raised in this slice


class CommandTimeoutError(OdooInstanceSdkError):
    """CLI command exceeded timeout."""  # ponytail: spec-mandated, not yet raised in this slice


class ProcessNotFoundError(OdooInstanceSdkError):
    """Process ID not found in registry."""


class ProcessExitedBeforeReady(OdooInstanceSdkError):
    """Linked process exited before readiness was reached."""


class ReadinessTimeoutError(OdooInstanceSdkError):
    """Readiness polling timed out."""

    def __init__(self, timeout: float, last_status: str | None = None) -> None:
        self.timeout = timeout
        self.last_status = last_status
        super().__init__(
            f"Readiness not reached within {timeout}s"
            + (f"; last status: {last_status}" if last_status else "")
        )


class InvalidBaseUrlError(OdooInstanceSdkError):
    """Invalid base URL format."""


class InstanceConfigurationError(OdooInstanceSdkError):
    """Invalid or incomplete instance configuration."""


class LogfileAccessError(InstanceConfigurationError):
    """The configured logfile cannot be opened for reading."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"logfile missing or unreadable: {path} ({reason}); "
            "set logfile in the bound odoo.conf and ensure the file exists"
        )


class MasterPasswordRequiredError(OdooInstanceSdkError):
    """Master password is required for this operation."""


class NonLocalInstanceError(OdooInstanceSdkError):
    """Operation not allowed on non-local instance."""


class DatabaseError(OdooInstanceSdkError):
    """Odoo database HTTP endpoint error."""

    def __init__(self, status_code: int, message: str, body: bytes) -> None:
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(message)


class BackupCatalogError(OdooInstanceSdkError):
    """Backup catalog operation failed."""


class BackupNotFoundError(OdooInstanceSdkError):
    """Backup not found in catalog."""


class BackupNotAvailableError(OdooInstanceSdkError):
    """Backup file is not available on disk."""


class BackupValidationUnavailableError(OdooInstanceSdkError):
    """Backup validation is not available for this backup."""


class DatabaseAlreadyExistsError(OdooInstanceSdkError):
    """Target database already exists."""


class RestoreFailedError(OdooInstanceSdkError):
    """Database restore failed."""


class DropFailedError(OdooInstanceSdkError):
    """Database drop failed."""


class BackupDownloadError(OdooInstanceSdkError):
    """Backup download failed."""


class DatabaseManagerUnavailableError(OdooInstanceSdkError):
    """Database manager endpoint unavailable or listing disabled."""


class PgAdminError(OdooInstanceSdkError):
    """Base for sanitized pgAdmin operation errors."""


class PgAdminEnvironmentNotFoundError(PgAdminError):
    """The requested environment is not present in the catalog."""

    def __init__(self) -> None:
        super().__init__("environment was not found")


class PgAdminNotEligibleError(PgAdminError):
    """The selected environment does not satisfy the pgAdmin preconditions."""

    def __init__(self) -> None:
        super().__init__("pgAdmin is not eligible for this environment")


class PgAdminDatabaseNotFoundError(PgAdminError):
    """The selected environment database is confirmed absent."""

    def __init__(self) -> None:
        super().__init__("selected database was not found")


class PgAdminUnavailableError(PgAdminError):
    """The pgAdmin operation could not safely complete."""

    def __init__(self) -> None:
        super().__init__("pgAdmin is unavailable")


class ProjectManifestNotFoundError(ConfigError):
    """Project manifest `.odcli/project.toml` not found."""

    def __init__(self, project_path: str, *, hint: str = "odcli init") -> None:
        self.project_path = project_path
        super().__init__(
            f"Project manifest not found at {project_path}/.odcli/project.toml — run {hint}"
        )


class VscodeImportError(ConfigError):
    """VS Code launch profile import failed."""


class ProjectContextError(OdooInstanceSdkError):
    """Project context could not be resolved."""


class EnvironmentNotFoundError(OdooInstanceSdkError):
    """Environment not found."""

    def __init__(self, selector: str) -> None:
        self.selector = selector
        super().__init__(f"Environment not found for selector {selector!r}")


class EnvironmentConflictError(OdooInstanceSdkError):
    """Environment conflict (duplicate active env, port, ambiguous selector)."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


class EnvironmentResolutionError(EnvironmentConflictError):
    """Environment selector could not be resolved unambiguously."""

    def __init__(self, message: str, *, candidates: list[str] | None = None) -> None:
        self.candidates = candidates or []
        super().__init__("environment_resolution", message, details={"candidates": self.candidates})


class LockConflictError(OdooInstanceSdkError):
    """An flock could not be acquired (held by another process)."""

    def __init__(self, lock_path: str, *, mode: str) -> None:
        self.lock_path = lock_path
        self.mode = mode
        super().__init__(f"Lock conflict on {lock_path} ({mode}): held by another process")


class PostgresClusterError(OdooInstanceSdkError):
    """Base for PostgresCluster lifecycle errors (messages are redacted)."""


class PostgresImageNotTrustedError(PostgresClusterError):
    """A repository-selected image was not explicitly approved by this user."""


class PostgresClusterNotOwnedError(PostgresClusterError):
    """stop() invoked on an externally owned cluster."""


class PostgresClusterUnreachableError(PostgresClusterError):
    """External cluster endpoint is not reachable during ensure_running()."""


class PostgresClusterUnhealthyError(PostgresClusterError):
    """Managed compose cluster is running but healthcheck fails."""


class PostgresClusterStartError(PostgresClusterError):
    """`docker compose up` failed (non-timeout)."""

    def __init__(self, message: str, *, returncode: int) -> None:
        self.returncode = returncode
        super().__init__(message)


class PostgresClusterStopError(PostgresClusterError):
    """`docker compose stop` failed (non-timeout)."""

    def __init__(self, message: str, *, returncode: int) -> None:
        self.returncode = returncode
        super().__init__(message)


class PostgresClusterTimeoutError(PostgresClusterError):
    """ensure_running() timed out before the cluster became healthy."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(f"Postgres cluster did not become healthy within {timeout}s")


class PostgresComposeUnavailableError(PostgresClusterError):
    """Docker / Docker Compose CLI is not available on PATH."""


class PostgresComposeInvalidError(PostgresClusterError):
    """Generated compose.yaml failed `docker compose config` validation."""


class PostgresPortCollisionError(PostgresClusterError):
    """Configured/persisted port is not free at compose up time."""


class MonitorError(OdooInstanceSdkError):
    """Monitor snapshot failed (messages are redacted)."""
