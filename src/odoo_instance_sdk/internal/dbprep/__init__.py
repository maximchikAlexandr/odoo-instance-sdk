"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import importlib

for _module_name in ("source_1", "source_2", "materialize"):
    _module = importlib.import_module(f"odoo_instance_sdk.internal.dbprep.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value
