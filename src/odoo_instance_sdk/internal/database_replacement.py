"""Compatibility re-export shim."""

from __future__ import annotations  # noqa: I001 -- keep the compatibility replacement re-exports grouped; remove when Ruff supports grouped aliases.

from odoo_instance_sdk.internal.dbreplace.planning import (
    CopyReplacementFailureContext as CopyReplacementFailureContext,
    _rename as _rename,
)
from odoo_instance_sdk.internal.dbreplace.validation import (
    build_copy_replacement_command as build_copy_replacement_command,
)
from odoo_instance_sdk.models import CopyReplacementResult as CopyReplacementResult

__all__ = [
    "CopyReplacementFailureContext",
    "CopyReplacementResult",
    "build_copy_replacement_command",
]
