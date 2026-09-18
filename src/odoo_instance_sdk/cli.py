"""Compatibility re-export shim."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

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


class _LazyExport(Protocol):
    """Typed boundary for callable compatibility exports resolved on demand."""

    def __call__(self, *args: str, **kwargs: str) -> str: ...


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


_LAZY_EXPORTS: dict[str, str] = {
    "_rich_vscode_generate": "odoo_instance_sdk.commands.cli_parts.callbacks",
    "build_launch_profile": "odoo_instance_sdk.internal.vscode_generate",
    "eval_expression_command": "odoo_instance_sdk.internal.automation",
    "exec_script_command": "odoo_instance_sdk.internal.automation",
    "export_translations_command": "odoo_instance_sdk.internal.automation",
    "get_catalog_path": "odoo_instance_sdk.internal.paths",
    "init_project_command": "odoo_instance_sdk.project_init",
    "list_modules_command": "odoo_instance_sdk.internal.automation",
    "module_tests_command": "odoo_instance_sdk.resources.testing",
    "resolve_module_test_selection": "odoo_instance_sdk.commands.test",
    "run_doctor": "odoo_instance_sdk.internal.doctor",
    "verify_deps_command": "odoo_instance_sdk.resources.deps",
}


def __getattr__(name: str) -> _LazyExport:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return cast("_LazyExport", value)


if __name__ == "__main__":
    cli()
