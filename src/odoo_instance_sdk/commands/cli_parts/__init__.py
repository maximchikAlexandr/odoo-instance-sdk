"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any


def __getattr__(name: str) -> Any:
    if name in {
        "cli",
        "init",
        "_run_doctor",
        "_run_shell_command",
        "_shell_failure",
        "_shell_payload",
        "_ShellCommandFailure",
        "_rich_shell_projection",
    }:
        module = import_module("odoo_instance_sdk.commands.cli_parts.registration")
        value = getattr(module, name)
        globals()[name] = value
        return value
    module = import_module("odoo_instance_sdk.commands.cli_parts.callbacks_a")
    value = getattr(module, name)
    globals()[name] = value
    return value
