"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import importlib

_SUBMODULES = ["backup_restore", "lifecycle"]

for _module_name in _SUBMODULES:
    _module = importlib.import_module(f"odoo_instance_sdk.resources.database.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value

from odoo_instance_sdk.resources.database.backup_restore_parts import DatabaseResource

__all__ = ["DatabaseResource"]
