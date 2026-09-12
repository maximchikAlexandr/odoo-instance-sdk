"""Test-only contracts and disposable fixtures for real-Odoo verification."""

from .archive import (
    ArchiveIdentity,
    ArchiveValidationError,
    SourceBackupPlan,
    archive_identity,
    odoo_initialization_command,
    source_database_name,
    target_sentinel_database_name,
)
from .cleanup import (
    FailureEvidence,
    LeakError,
    LeakReport,
    ResourceLedger,
    ResourceRecord,
    audit_no_leaks,
    compose_down,
    write_odoo_config,
    write_owner_only_secret,
)
from .compose import (
    ComposeTopology,
    PortReservation,
    new_run_id,
    reserve_loopback_port,
    reserve_ports,
)
from .contracts import ContractError, validate_leaf_metadata
from .pins import E2E_PINS, PHASE_BUDGETS, PhaseBudget, budget_for

__all__ = [
    "E2E_PINS",
    "PHASE_BUDGETS",
    "ArchiveIdentity",
    "ArchiveValidationError",
    "ComposeTopology",
    "ContractError",
    "FailureEvidence",
    "LeakError",
    "LeakReport",
    "PhaseBudget",
    "PortReservation",
    "ResourceLedger",
    "ResourceRecord",
    "SourceBackupPlan",
    "archive_identity",
    "audit_no_leaks",
    "budget_for",
    "compose_down",
    "new_run_id",
    "odoo_initialization_command",
    "reserve_loopback_port",
    "reserve_ports",
    "source_database_name",
    "target_sentinel_database_name",
    "validate_leaf_metadata",
    "write_odoo_config",
    "write_owner_only_secret",
]
