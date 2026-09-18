from __future__ import annotations

from typing import TYPE_CHECKING

import odoo_instance_sdk.cli as cli


def test_lazy_export_registry_is_closed_and_statistically_contract_checked() -> None:
    assert tuple(cli._LAZY_EXPORTS) == (
        "_rich_vscode_generate",
        "build_launch_profile",
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
    )

    if TYPE_CHECKING:
        from typing import TypeVar

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

        Contract = TypeVar("Contract")

        def check_contract(expected: Contract, actual: Contract) -> None:
            pass

        check_contract(
            cli._rich_vscode_generate,
            _rich_vscode_generate,
        )
        check_contract(
            cli.build_launch_profile,
            build_launch_profile,
        )
        check_contract(
            cli.eval_expression_command,
            eval_expression_command,
        )
        check_contract(
            cli.exec_script_command,
            exec_script_command,
        )
        check_contract(
            cli.export_translations_command,
            export_translations_command,
        )
        check_contract(
            cli.get_catalog_path,
            get_catalog_path,
        )
        check_contract(
            cli.init_project_command,
            init_project_command,
        )
        check_contract(
            cli.list_modules_command,
            list_modules_command,
        )
        check_contract(
            cli.module_tests_command,
            module_tests_command,
        )
        check_contract(
            cli.resolve_module_test_selection,
            resolve_module_test_selection,
        )
        check_contract(
            cli.run_doctor,
            run_doctor,
        )
        check_contract(
            cli.verify_deps_command,
            verify_deps_command,
        )
