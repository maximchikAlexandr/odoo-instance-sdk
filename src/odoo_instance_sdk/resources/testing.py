"""Public Odoo test runner primitives."""

from __future__ import annotations

from odoo_instance_sdk.internal.automation import (
    module_tests_command,
    run_odoo_tests,
    run_odoo_tests_command,
)
from odoo_instance_sdk.models import TestCommandSnapshot

__all__ = [
    "TestCommandSnapshot",
    "module_tests_command",
    "run_odoo_tests",
    "run_odoo_tests_command",
]
