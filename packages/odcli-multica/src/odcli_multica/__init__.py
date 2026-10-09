"""Public typed Multica/Odoo composition primitives."""

from odcli_multica.client import MulticaOdooClient, PrepareCommand
from odcli_multica.models import (
    ContextRequest,
    MulticaCompatibility,
    PreparationRequest,
    TaskContext,
    VerifiedTaskContext,
)

__all__ = [
    "ContextRequest",
    "MulticaCompatibility",
    "MulticaOdooClient",
    "PreparationRequest",
    "PrepareCommand",
    "TaskContext",
    "VerifiedTaskContext",
]
