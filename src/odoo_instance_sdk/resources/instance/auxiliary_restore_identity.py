"""Compatibility imports for the shared runtime identity proof."""

from odoo_instance_sdk.resources.instance.runtime_identity import (
    _expected_runtime_identity,
    _listener_owner_pids,
    _project_runtime_owns_port,
    _recorded_runtime_pid,
    _runtime_process_matches,
    _runtime_row_matches,
    _socket_owned_by,
)

__all__ = [
    "_expected_runtime_identity",
    "_listener_owner_pids",
    "_project_runtime_owns_port",
    "_recorded_runtime_pid",
    "_runtime_process_matches",
    "_runtime_row_matches",
    "_socket_owned_by",
]
