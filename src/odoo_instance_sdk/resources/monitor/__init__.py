"""Public environment-monitor resource entry point."""

from __future__ import annotations  # noqa: I001 -- keep public monitor planning re-exports grouped; remove when Ruff supports grouped aliases.

from odoo_instance_sdk.resources.monitor.collection_parts import (
    EnvironmentMonitor as EnvironmentMonitor,
)
from odoo_instance_sdk.internal.monitor_lifecycle import (
    monitor_logs_command as monitor_logs_command,
    monitor_status_command as monitor_status_command,
    restart_monitor_command as restart_monitor_command,
    serve_monitor as serve_monitor,
    serve_monitor_command as serve_monitor_command,
    stop_monitor_command as stop_monitor_command,
)
from odoo_instance_sdk.resources.monitor.planning import (
    SnapshotSelection as SnapshotSelection,
    select_snapshot_environment as select_snapshot_environment,
)

__all__ = [
    "EnvironmentMonitor",
    "SnapshotSelection",
    "monitor_logs_command",
    "monitor_status_command",
    "restart_monitor_command",
    "select_snapshot_environment",
    "serve_monitor",
    "serve_monitor_command",
    "stop_monitor_command",
]
