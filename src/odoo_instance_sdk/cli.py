"""Compatibility re-export shim."""

from __future__ import annotations

import importlib

_package = importlib.import_module("odoo_instance_sdk.commands.cli_parts")
for _key, _value in _package.__dict__.items():
    if _key.startswith("__"):
        continue
    globals()[_key] = _value

from odoo_instance_sdk.commands.cli_parts.callbacks_a import (
    _rich_vscode_generate as _rich_vscode_generate,
)
from odoo_instance_sdk.commands.cli_parts.registration import (
    _rich_shell_projection as _rich_shell_projection,
)
from odoo_instance_sdk.commands.cli_parts.registration import (
    _run_doctor as _run_doctor,
)
from odoo_instance_sdk.commands.cli_parts.registration import (
    _run_shell_command as _run_shell_command,
)
from odoo_instance_sdk.commands.cli_parts.registration import (
    _shell_failure as _shell_failure,
)
from odoo_instance_sdk.commands.cli_parts.registration import (
    _shell_payload as _shell_payload,
)
from odoo_instance_sdk.commands.cli_parts.registration import (
    _ShellCommandFailure as _ShellCommandFailure,
)
from odoo_instance_sdk.commands.cli_parts.registration import (
    cli as cli,
)
from odoo_instance_sdk.resources.deps import verify_deps_command as verify_deps_command
