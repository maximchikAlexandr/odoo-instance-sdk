"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import importlib

_SUBMODULES = ["collection", "planning", "projection"]

for _module_name in _SUBMODULES:
    _module = importlib.import_module(f"odoo_instance_sdk.resources.monitor.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value

from odoo_instance_sdk.resources.monitor.collection_parts import EnvironmentMonitor
from odoo_instance_sdk.resources.monitor.planning import (
    SnapshotSelection,
    select_snapshot_environment,
)

__all__ = ["EnvironmentMonitor", "SnapshotSelection", "select_snapshot_environment"]
