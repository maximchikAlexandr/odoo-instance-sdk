"""Compose one captured post-success automatic backup prune phase."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

import msgspec

from odoo_instance_sdk.internal.proc import PreparedAction, active_context, prepared_command
from odoo_instance_sdk.models import (
    Backup,
    BackupPrunePlan,
    DatabasePreparationResult,
    DevelopmentEnvironment,
    EnvironmentCheckoutResult,
    RestoreResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import RunContext
    from odoo_instance_sdk.models import BackupRetentionPolicy
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.backup import BackupResource

T = TypeVar("T")
if TYPE_CHECKING:
    Project = ProjectConfig | Path | str
else:
    Project = Path | str

_ACTION_ID = "backup.auto-prune"
_SKIPPED_WARNING = "automatic backup pruning was skipped"
_FAILED_WARNING = "automatic backup pruning failed; primary operation succeeded"
_PARTIAL_WARNING = "automatic backup pruning completed with warnings"


def attach_auto_prune(
    command: Command[T],
    *,
    backups: BackupResource,
    project: Project | None,
    excluded_ids: tuple[uuid.UUID, ...] = (),
) -> Command[T]:
    """Append one captured prune action to an eligible project-aware command."""
    if project is None or active_context() is not None:
        return command

    from odoo_instance_sdk.internal.backup_retention import read_retention_policy

    policy: BackupRetentionPolicy | None = None
    plan: BackupPrunePlan | None = None
    warning: str | None = None
    try:
        policy = read_retention_policy()
        if policy.auto_prune:
            plan = backups._build_prune_plan(project, policy=policy)
    except Exception:
        warning = _SKIPPED_WARNING

    if policy is not None and not policy.auto_prune and warning is None:
        return command

    from odoo_instance_sdk.execution import Command, ExecutionPlan

    prepared = command._prepared()
    action = PreparedAction(
        step_id=_ACTION_ID,
        action=_ACTION_ID,
        description="Run captured automatic backup retention after primary success",
        read_only=plan is None,
        mutating=plan is not None,
    )
    steps = (*prepared.steps, action)

    def execute(context: RunContext[T]) -> T:
        primary = prepared.callback(context)
        context.action(_ACTION_ID)
        maintenance_warning = warning
        if plan is not None:
            try:
                excluded = set(excluded_ids) | set(_result_backup_ids(primary))
                result = backups._execute_prune_plan(
                    plan,
                    dry_run=False,
                    excluded_ids=tuple(excluded),
                )
                if result.warnings or result.failed_ids or result.failures:
                    maintenance_warning = _PARTIAL_WARNING
            except Exception:
                maintenance_warning = _FAILED_WARNING
        context.complete_action(_ACTION_ID)
        return _append_warning(primary, maintenance_warning)

    public_plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        observations=command.plan.observations,
        warnings=command.plan.warnings + ((warning,) if warning is not None else ()),
    ).with_fingerprint()
    return cast(
        "Command[T]",
        Command.from_prepared(
            public_plan,
            prepared_command(
                execute,
                steps,
                executor=prepared.executor,
                private_projection=prepared.private_projection,
            ),
        ),
    )


def _result_backup_ids(result: object) -> tuple[uuid.UUID, ...]:
    if isinstance(result, Backup):
        return (result.id,)
    if isinstance(result, RestoreResult):
        return (result.source.id,)
    if isinstance(result, DatabasePreparationResult):
        return (result.backup.id,) if result.backup is not None else ()
    if isinstance(result, DevelopmentEnvironment):
        return (result.backup_id,) if result.backup_id is not None else ()
    if isinstance(result, EnvironmentCheckoutResult):
        backup_id = result.environment.backup_id
        return (backup_id,) if backup_id is not None else ()
    return ()


def _append_warning(result: T, warning: str | None) -> T:
    if warning is None:
        return result
    if isinstance(
        result,
        (Backup, RestoreResult, DatabasePreparationResult, DevelopmentEnvironment),
    ):
        return cast("T", msgspec.structs.replace(result, warnings=(*result.warnings, warning)))
    if isinstance(result, EnvironmentCheckoutResult):
        return cast(
            "T",
            msgspec.structs.replace(
                result,
                warnings=(*result.warnings, warning),
                plan=msgspec.structs.replace(
                    result.plan,
                    warnings=(*result.plan.warnings, warning),
                ),
            ),
        )
    return result


__all__ = ["attach_auto_prune"]
