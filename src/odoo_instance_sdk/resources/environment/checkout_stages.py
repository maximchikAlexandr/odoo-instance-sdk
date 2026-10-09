"""Pure checkout planning stage adapters."""

from __future__ import annotations

from dataclasses import replace

import msgspec

from odoo_instance_sdk.exceptions import PlanValidationError
from odoo_instance_sdk.models import EnvironmentDatabaseMode
from odoo_instance_sdk.resources.environment.checkout_planning import (
    _CheckoutPlanningState,
    _CheckoutSnapshot,
    _execution_plan,
    _ExpressionApi,
    _ExpressionResult,
    _PlanningError,
    _PlanningOutcome,
    _public_checkout_plan,
)


def planning_result(expression_api: _ExpressionApi, outcome: _PlanningOutcome) -> _ExpressionResult:
    if outcome.error is not None:
        return expression_api.Error(outcome.error)
    if outcome.state is None:
        return expression_api.Error(PlanValidationError("checkout stage produced no state"))
    return expression_api.Ok(outcome.state)


def planning_error_outcome(error: _PlanningError) -> _PlanningOutcome:
    return _PlanningOutcome(error=error)


def validate_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    plan = state.private
    if not plan.branch.strip():
        return _PlanningOutcome(error=PlanValidationError("checkout branch must not be empty"))
    if not plan.worktree_argv:
        return _PlanningOutcome(error=PlanValidationError("checkout planning produced no command"))
    if plan.db_mode is EnvironmentDatabaseMode.COPY and plan.target_database is None:
        return _PlanningOutcome(
            error=PlanValidationError("copy checkout requires a target database")
        )
    return _PlanningOutcome(state=state)


def normalize_checkout_projections(state: _CheckoutPlanningState) -> _PlanningOutcome:
    """Normalize captured provenance before building either checkout projection."""
    provenance = msgspec.structs.replace(
        state.provenance,
        source_name=state.private.source_name,
        source_base_url=state.private.source_base_url,
        resolved_base_revision=state.private.base_revision,
        backup_id=(
            state.private.selected_backup.id
            if state.private.selected_backup is not None
            else state.provenance.backup_id
        ),
    )
    public = _public_checkout_plan(state.private, provenance, state.freshness, state.warnings)
    execution_plan = _execution_plan(state.private, provenance, state.freshness, state.warnings)
    return _PlanningOutcome(
        state=replace(state, provenance=provenance, public=public, execution_plan=execution_plan)
    )


def normalize_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    return normalize_checkout_projections(state)


def capture_checkout_stage(state: _CheckoutPlanningState) -> _PlanningOutcome:
    if state.public is None or state.execution_plan is None:
        return _PlanningOutcome(error=PlanValidationError("checkout projections are incomplete"))
    snapshot = _CheckoutSnapshot(
        private=state.private,
        public=state.public,
        execution_plan=state.execution_plan,
    )
    return _PlanningOutcome(state=replace(state, snapshot=snapshot))
