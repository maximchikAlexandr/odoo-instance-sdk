"""Compatibility re-export shim."""

from __future__ import annotations

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.cli_parts.callbacks import _rich_vscode_generate
from odoo_instance_sdk.commands.cli_parts.registration import (
    _rich_shell_projection,
    _run_shell_command,
    _shell_failure,
    _shell_payload,
    _ShellCommandFailure,
    cli,
)
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

if __name__ == "__main__":
    cli()
