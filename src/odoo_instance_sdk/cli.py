"""Compatibility re-export shim."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.cli_parts.registration import (
    _rich_shell_projection,
    _run_shell_command,
    _shell_failure,
    _shell_payload,
    _ShellCommandFailure,
    cli,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.commands.cli_parts.callbacks import _rich_vscode_generate
    from odoo_instance_sdk.commands.test import resolve_module_test_selection
    from odoo_instance_sdk.internal.automation import (
        eval_expression_command,
        exec_script_command,
        export_translations_command,
        list_modules_command,
    )
    from odoo_instance_sdk.internal.doctor import run_doctor
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.internal.vscode_generate import build_launch_profile
    from odoo_instance_sdk.project_init import init_project_command
    from odoo_instance_sdk.resources.deps import verify_deps_command
    from odoo_instance_sdk.resources.testing import module_tests_command

__all__ = [
    "_ShellCommandFailure",
    "_rich_shell_projection",
    "_rich_vscode_generate",
    "_run_shell_command",
    "_shell_failure",
    "_shell_payload",
    "build_launch_profile",
    "cli",
    "cli_context",
    "eval_expression_command",
    "exec_script_command",
    "export_translations_command",
    "get_catalog_path",
    "init_project_command",
    "list_modules_command",
    "module_tests_command",
    "resolve_module_test_selection",
    "run_doctor",
    "verify_deps_command",
]

_LazyExport = Callable[..., object]

_LAZY_EXPORTS = {
    "_rich_vscode_generate": ("odoo_instance_sdk.commands.cli_parts.callbacks", None),
    "build_launch_profile": ("odoo_instance_sdk.internal.vscode_generate", None),
    "eval_expression_command": ("odoo_instance_sdk.internal.automation", None),
    "exec_script_command": ("odoo_instance_sdk.internal.automation", None),
    "export_translations_command": ("odoo_instance_sdk.internal.automation", None),
    "get_catalog_path": ("odoo_instance_sdk.internal.paths", None),
    "init_project_command": ("odoo_instance_sdk.project_init", None),
    "list_modules_command": ("odoo_instance_sdk.internal.automation", None),
    "module_tests_command": ("odoo_instance_sdk.resources.testing", None),
    "resolve_module_test_selection": ("odoo_instance_sdk.commands.test", None),
    "run_doctor": ("odoo_instance_sdk.internal.doctor", None),
    "verify_deps_command": ("odoo_instance_sdk.resources.deps", None),
}


def __getattr__(name: str) -> _LazyExport:
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(spec[0])
    value = getattr(module, name)
    globals()[name] = value
    return cast("_LazyExport", value)


if __name__ == "__main__":
    cli()
