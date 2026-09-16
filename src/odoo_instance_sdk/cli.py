"""Compatibility re-export shim."""

from __future__ import annotations

from typing import Any

from odoo_instance_sdk.commands.cli_parts.registration import cli as cli

_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    "_ShellCommandFailure": (
        "odoo_instance_sdk.commands.cli_parts.registration",
        "_ShellCommandFailure",
    ),
    "_rich_shell_projection": (
        "odoo_instance_sdk.commands.cli_parts.registration",
        "_rich_shell_projection",
    ),
    "_rich_vscode_generate": (
        "odoo_instance_sdk.commands.cli_parts.callbacks_a",
        "_rich_vscode_generate",
    ),
    "_run_doctor": ("odoo_instance_sdk.commands.cli_parts.registration", "_run_doctor"),
    "_run_shell_command": (
        "odoo_instance_sdk.commands.cli_parts.registration",
        "_run_shell_command",
    ),
    "_shell_failure": ("odoo_instance_sdk.commands.cli_parts.registration", "_shell_failure"),
    "_shell_payload": ("odoo_instance_sdk.commands.cli_parts.registration", "_shell_payload"),
    "build_launch_profile": (
        "odoo_instance_sdk.internal.vscode_generate",
        "build_launch_profile",
    ),
    "eval_expression_command": (
        "odoo_instance_sdk.internal.automation",
        "eval_expression_command",
    ),
    "exec_script_command": ("odoo_instance_sdk.internal.automation", "exec_script_command"),
    "export_translations_command": (
        "odoo_instance_sdk.internal.automation",
        "export_translations_command",
    ),
    "get_catalog_path": ("odoo_instance_sdk.internal.paths", "get_catalog_path"),
    "list_modules_command": ("odoo_instance_sdk.internal.automation", "list_modules_command"),
    "module_tests_command": ("odoo_instance_sdk.resources.testing", "module_tests_command"),
    "resolve_module_test_selection": (
        "odoo_instance_sdk.commands.test",
        "resolve_module_test_selection",
    ),
    "run_doctor": ("odoo_instance_sdk.internal.doctor", "run_doctor"),
    "update_modules_command": (
        "odoo_instance_sdk.internal.automation",
        "update_modules_command",
    ),
    "verify_deps_command": ("odoo_instance_sdk.resources.deps", "verify_deps_command"),
}


def __getattr__(name: str) -> Any:
    spec = _LAZY_EXPORTS.get(name)
    if spec is not None:
        module_name, attr_name = spec
        from importlib import import_module

        module = import_module(module_name)
        value = module if attr_name is None else getattr(module, attr_name)
        globals()[name] = value
        return value
    if name == "cli_context":
        from importlib import import_module

        value = import_module("odoo_instance_sdk.commands.context")
        globals()[name] = value
        return value
    package = __import__("odoo_instance_sdk.commands.cli_parts", fromlist=[name])
    if hasattr(package, name):
        value = getattr(package, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    cli()
