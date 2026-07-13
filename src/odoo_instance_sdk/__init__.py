"""Odoo Instance SDK — typed Python API for managing local Odoo 19.0 instances."""

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.exceptions import (
    CommandTimeoutError,
    ConfigError,
    DatabaseError,
    OdooInstanceSdkError,
    ProcessExitedBeforeReady,
    ProcessNotFoundError,
    ReadinessTimeoutError,
    RemoteInstanceError,
)
from odoo_instance_sdk.models import (
    BackupArtifact,
    CommandResult,
    DropResult,
    OdooClientConfig,
    OdooProcess,
    ProcessStatus,
    ReadinessResult,
    RestoreResult,
    StartConfig,
)

__version__ = "0.1.0"
__all__ = [
    "BackupArtifact",
    "CommandResult",
    "CommandTimeoutError",
    "ConfigError",
    "DatabaseError",
    "DropResult",
    "OdooClient",
    "OdooClientConfig",
    "OdooInstanceSdkError",
    "OdooProcess",
    "ProcessExitedBeforeReady",
    "ProcessNotFoundError",
    "ProcessStatus",
    "ReadinessResult",
    "ReadinessTimeoutError",
    "RemoteInstanceError",
    "RestoreResult",
    "StartConfig",
]
