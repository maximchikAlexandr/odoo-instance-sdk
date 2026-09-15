"""Compatibility re-export shim for the split proc executor."""

from __future__ import annotations

import odoo_instance_sdk.internal.proc.run as _run
from odoo_instance_sdk.internal.proc.run import (
    ProcessExecutionError,
    ProcessHandle,
    ProcessResult,
    ProcessSpawnError,
    ProcessTimeoutError,
    SubprocessExecutor,
    owned_handle,
    prepared_step,
    run_captured,
    run_captured_limited,
)
from odoo_instance_sdk.internal.proc.spawn import spawn
from odoo_instance_sdk.internal.proc.terminate import (
    is_process_alive,
    terminate,
    terminate_pid,
    wait_foreground,
)

globals().update(
    {
        name: value
        for name, value in _run.__dict__.items()
        if name.startswith("_") and not name.startswith("__")
    }
)

__all__ = [
    "ProcessExecutionError",
    "ProcessHandle",
    "ProcessResult",
    "ProcessSpawnError",
    "ProcessTimeoutError",
    "SubprocessExecutor",
    "is_process_alive",
    "owned_handle",
    "prepared_step",
    "run_captured",
    "run_captured_limited",
    "spawn",
    "terminate",
    "terminate_pid",
    "wait_foreground",
]
