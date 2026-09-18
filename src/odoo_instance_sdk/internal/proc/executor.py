"""Compatibility re-export shim for the split proc executor."""

from __future__ import annotations

import odoo_instance_sdk.internal.proc.run as _run
from odoo_instance_sdk.internal.proc import PreparedStep, StepObserver
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
from odoo_instance_sdk.internal.proc.run import (
    _environment as _environment,
)
from odoo_instance_sdk.internal.proc.run import (
    _run_pump as _run_pump_impl,
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
        if name.startswith("_") and not name.startswith("__") and name != "_run_pump"
    }
)


def _run_pump(
    prepared: PreparedStep,
    *,
    timeout: float | None,
    environment_snapshot: tuple[tuple[str, str], ...],
    observer: StepObserver | None,
    observe_output: bool,
    max_output_bytes: int | None = None,
) -> tuple[int, bytes, bytes, float]:
    return _run_pump_impl(
        prepared,
        timeout=timeout,
        environment_snapshot=environment_snapshot,
        observer=observer,
        observe_output=observe_output,
        max_output_bytes=max_output_bytes,
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
