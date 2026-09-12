"""Test-only contracts for the reproducible real-Odoo verification tiers."""

from .contracts import ContractError, validate_leaf_metadata
from .pins import E2E_PINS, PHASE_BUDGETS, PhaseBudget, budget_for

__all__ = [
    "E2E_PINS",
    "PHASE_BUDGETS",
    "ContractError",
    "PhaseBudget",
    "budget_for",
    "validate_leaf_metadata",
]
