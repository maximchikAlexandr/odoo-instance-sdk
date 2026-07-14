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
