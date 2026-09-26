"""Shared, fail-closed proof for an SDK-owned Odoo runtime."""

from .auxiliary_restore_identity import (
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
