"""Database replacement package."""

from __future__ import annotations

from odoo_instance_sdk.internal.dbreplace.planning import (
    CopyReplacementFailureContext,
    _rename,
)
from odoo_instance_sdk.internal.dbreplace.validation import build_copy_replacement_command

__all__ = [
    "CopyReplacementFailureContext",
    "_rename",
    "build_copy_replacement_command",
]
