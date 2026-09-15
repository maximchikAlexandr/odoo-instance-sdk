"""Split helper modules."""

from __future__ import annotations

import importlib

for _module_name in ("helpers_1", "helpers_2"):
    _module = importlib.import_module(
        f"odoo_instance_sdk.resources.instance.{_module_name}",
    )
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value
