"""Public environment-monitor resource entry point."""

from __future__ import annotations  # noqa: I001 -- keep public monitor planning re-exports grouped; remove when Ruff supports grouped aliases.

from odoo_instance_sdk.resources.monitor.collection_parts import (
    EnvironmentMonitor as EnvironmentMonitor,
)
from odoo_instance_sdk.resources.monitor.planning import (
    SnapshotSelection as SnapshotSelection,
    select_snapshot_environment as select_snapshot_environment,
)

__all__ = ["EnvironmentMonitor", "SnapshotSelection", "select_snapshot_environment"]
