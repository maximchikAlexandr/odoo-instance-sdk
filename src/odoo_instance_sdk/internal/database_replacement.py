"""Compatibility re-export shim."""

from __future__ import annotations

import importlib

from odoo_instance_sdk.internal.dbreplace.planning import (
    CopyReplacementFailureContext as CopyReplacementFailureContext,
)
from odoo_instance_sdk.internal.dbreplace.planning import _rename as _rename
from odoo_instance_sdk.internal.dbreplace.validation import (
    build_copy_replacement_command as build_copy_replacement_command,
)
from odoo_instance_sdk.models import CopyReplacementResult as CopyReplacementResult

_package = importlib.import_module("odoo_instance_sdk.internal.dbreplace")
for _key, _value in _package.__dict__.items():
    if _key.startswith("__"):
        continue
    globals()[_key] = _value

__all__ = [
    "CopyReplacementFailureContext",
    "build_copy_replacement_command",
]
