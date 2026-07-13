from __future__ import annotations


class OdooInstanceSdkError(Exception):
    """Base exception for all SDK errors."""


class ConfigError(OdooInstanceSdkError):
    """Invalid configuration."""


class CommandTimeoutError(OdooInstanceSdkError):
    """CLI command exceeded timeout."""


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


class RemoteInstanceError(OdooInstanceSdkError):
    """Operation not allowed on non-local instance."""


class DatabaseError(OdooInstanceSdkError):
    """Odoo database HTTP endpoint error."""

    def __init__(self, status_code: int, message: str, body: bytes) -> None:
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(message)
