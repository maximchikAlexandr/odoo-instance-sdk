"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import importlib

_SUBMODULES = ["backup_restore", "diagnostics", "lifecycle"]

for _module_name in _SUBMODULES:
    _module = importlib.import_module(f"odoo_instance_sdk.resources.postgres.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value

from odoo_instance_sdk.resources.postgres.backup_restore_parts import PostgresCluster
from odoo_instance_sdk.resources.postgres.lifecycle import _resolve_project_id

__all__ = ["PostgresCluster", "_resolve_project_id"]
