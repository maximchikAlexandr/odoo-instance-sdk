"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import importlib

_SUBMODULES = ["registration", "callbacks_a"]

for _module_name in _SUBMODULES:
    _module = importlib.import_module(f"odoo_instance_sdk.commands.cli_parts.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value
