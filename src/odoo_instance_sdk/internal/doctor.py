"""Compatibility re-export shim."""

from __future__ import annotations

import importlib

_package = importlib.import_module("odoo_instance_sdk.internal.doctor")
for _key, _value in _package.__dict__.items():
    if _key.startswith("__"):
        continue
    globals()[_key] = _value
