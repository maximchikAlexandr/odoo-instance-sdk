from __future__ import annotations

import sys

from odoo_instance_sdk.resources.instance.runtime import (
    _runtime_argv_mismatch_components,
    _RuntimeIdentity,
)


def validate_runtime_identity(identity: _RuntimeIdentity) -> None:
    if identity.vanished:
        return
    mismatches: list[str] = []
    if identity.live_create_time != identity.create_time:
        mismatches.append("create_time")
    if identity.live_executable != identity.expected_executable:
        mismatches.append("executable")
    mismatches.extend(_runtime_argv_mismatch_components(identity))
    if identity.live_cwd != identity.expected_cwd:
        mismatches.append("cwd")
    if (
        identity.live_config_path != identity.expected_config_path
        and "argv: --config" not in mismatches
    ):
        mismatches.append("config")
    if sys.platform != "win32" and identity.process_group_id != identity.root_pid:
        mismatches.append("process_group")
    if mismatches:
        raise RuntimeError(f"runtime identity mismatch: {', '.join(mismatches)}")
