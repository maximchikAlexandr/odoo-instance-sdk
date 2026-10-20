"""Project-removal command composition and the non-purge lifecycle boundary.

The coordinator owns selection, one immutable action ledger, the project
lifecycle lock, and the terminal catalogue transition.  Resource-specific
purge work is deliberately represented by sealed adapter protocols so the
runtime and data packages can consume the same captured decisions later.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

import msgspec

from odoo_instance_sdk.exceptions import ProjectRemovalError
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.locks import exclusive_lock, project_manifest_lock_path
from odoo_instance_sdk.internal.proc import PreparedAction, RunContext
from odoo_instance_sdk.internal.project_removal import (
    ProjectRegistration,
    ProjectRemovalProjection,
    RemovalRuntimeSource,
    build_project_removal_projection,
    resolve_project_selector,
)
from odoo_instance_sdk.internal.resource_inventory import (
    ActiveUse,
    EnvironmentResourceSource,
    OwnershipConfidence,
    ProjectResourceSource,
    collect_resource_inventory,
)
from odoo_instance_sdk.models.project_removal import (
    ProjectRemovalErrorDetails,
    ProjectRemovalResult,
    ProjectResourceDecision,
    ProjectSelector,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


class ProjectRemovalAdapter(Protocol):
    """Sealed input/output contract for a purge resource family."""

    def apply(
        self,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> ProjectResourceDecision: ...


class ProjectRemovalAdapters(Protocol):
    """Optional family adapters consumed by later purge work packages."""

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None: ...


class _NoPurgeAdapters:
    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        del decision
        return None


def _row_registration(row: object) -> ProjectRegistration:
    return ProjectRegistration(
        project_id=str(row["project_id"]),  # type: ignore[index]
        repository_root=Path(str(row["repository_root"])),  # type: ignore[index]
        git_common_dir=Path(str(row["git_common_dir"])),  # type: ignore[index]
        registration_state=cast(
            "Literal['registered', 'forgotten']",
            str(row["registration_state"]),  # type: ignore[index]
        ),
    )


def _environment_sources(
    catalog: BackupCatalog, project_id: str
) -> tuple[EnvironmentResourceSource, ...]:
    rows = catalog._conn.execute(
        "SELECT * FROM environments WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    return tuple(
        EnvironmentResourceSource(
            environment_id=str(row["id"]),
            project_id=project_id,
            name=str(row["name"]),
            worktree_path=Path(str(row["worktree_path"])),
            python_environment_path=Path(str(row["python_environment_path"])),
            python_environment_owned=bool(row["python_environment_owned"]),
            state=str(row["state"]),
            backup_id=str(row["backup_id"]) if row["backup_id"] else None,
            last_error=str(row["last_error"]) if row["last_error"] else None,
        )
        for row in rows
    )


def _project_runtime(catalog: BackupCatalog, project_id: str) -> tuple[RemovalRuntimeSource, ...]:
    row = catalog.get_runtime("project", project_id)
    if row is None:
        return ()
    return (
        RemovalRuntimeSource(
            project_id=project_id,
            identity=f"runtime:project:{project_id}",
            active_use=ActiveUse.ACTIVE,
            ownership=OwnershipConfidence.PROVEN,
            stale=False,
        ),
    )


def _projection(
    catalog: BackupCatalog, registration: ProjectRegistration
) -> ProjectRemovalProjection:
    environments = _environment_sources(catalog, registration.project_id)
    inventory = collect_resource_inventory(
        catalog=catalog,
        projects=(
            ProjectResourceSource(
                project_id=registration.project_id,
                repository_root=registration.repository_root,
            ),
        ),
        environments=environments,
        project_id=registration.project_id,
        roots=(registration.repository_root,),
    )
    return build_project_removal_projection(
        registration=registration,
        inventory=inventory,
        runtimes=_project_runtime(catalog, registration.project_id),
    )


def _forget_blockers(catalog: BackupCatalog, project_id: str) -> tuple[str, ...]:
    blockers: set[str] = set()
    if catalog.get_runtime("project", project_id) is not None:
        blockers.add(f"runtime:project:{project_id}")
    for row in catalog._conn.execute(
        "SELECT id, state FROM environments WHERE project_id = ? ORDER BY id", (project_id,)
    ):
        if str(row["state"]) != "removed":
            blockers.add(f"environment:{row['id']}")
    return tuple(sorted(blockers))


def _action_step(decision: ProjectResourceDecision) -> PreparedAction:
    return PreparedAction(
        step_id=f"project-removal:{decision.action}:{decision.identity}",
        action=decision.action,
        description=f"{decision.action} {decision.identity}",
        details={"identity": decision.identity, "reason": decision.reason},
        mutating=decision.action != "retain",
    )


def _error_details(
    projection: ProjectRemovalProjection,
    *,
    mode: Literal["forget", "purge"],
    failed_step: str,
) -> ProjectRemovalErrorDetails:
    public = projection.public_decisions
    return ProjectRemovalErrorDetails(
        project_id=projection.project_id,
        mode=mode,
        registration_state=projection.registration_state,
        retained=tuple(item for item in public if item.action == "retain"),
        remaining=tuple(item for item in public if item.action != "retain"),
        failed_step=failed_step,
    )


def _error(
    message: str,
    projection: ProjectRemovalProjection,
    *,
    mode: Literal["forget", "purge"],
    failed_step: str,
) -> ProjectRemovalError:
    details = _error_details(projection, mode=mode, failed_step=failed_step)
    builtins = cast("dict[str, JsonValue]", msgspec.to_builtins(details))
    return ProjectRemovalError(message, details={"removal": builtins})


def capture_project_removal_command(  # noqa: C901
    catalog: BackupCatalog,
    selector: ProjectSelector,
    *,
    purge: bool = False,
    confirmation: str | None = None,
    adapters: ProjectRemovalAdapters | None = None,
) -> Command[ProjectRemovalResult]:
    """Capture one project-removal plan; execution revalidates it under lock."""
    registrations = tuple(
        _row_registration(row) for row in catalog.list_projects(include_forgotten=True)
    )
    registration = resolve_project_selector(selector, registrations)
    projection = _projection(catalog, registration)
    mode: Literal["forget", "purge"] = "purge" if purge else "forget"
    decisions: tuple[ProjectResourceDecision, ...]
    if not purge:
        blockers = _forget_blockers(catalog, registration.project_id)
        if blockers and registration.registration_state == "registered":
            raise _error(
                "project cannot be forgotten while managed resources are active: "
                + ", ".join(blockers),
                projection,
                mode=mode,
                failed_step="project-removal:preflight",
            )
        decisions = (
            ProjectResourceDecision(
                identity=f"project:{registration.project_id}",
                resource_type="project",
                action="forget",
                reason="registration_transition",
                target="<catalogue>",
            ),
        )
    else:
        decisions = projection.public_decisions
    steps = tuple(_action_step(decision) for decision in decisions)
    plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        warnings=(),
        fingerprint=projection.fingerprint,
    )
    adapter_set = adapters or _NoPurgeAdapters()

    def run(context: RunContext[ProjectRemovalResult]) -> ProjectRemovalResult:  # noqa: C901
        lock = exclusive_lock(project_manifest_lock_path(registration.project_id))
        with lock:
            row = catalog.get_project_for_lifecycle(registration.project_id)
            if row is None:
                raise _error(
                    "project registration disappeared",
                    projection,
                    mode=mode,
                    failed_step="project-removal:revalidate",
                )
            current = _row_registration(row)
            current_projection = _projection(catalog, current)
            if current.repository_root.resolve() != registration.repository_root.resolve():
                raise _error(
                    "project registration identity changed",
                    projection,
                    mode=mode,
                    failed_step="project-removal:revalidate",
                )
            if current_projection.fingerprint != projection.fingerprint:
                raise _error(
                    "project removal plan is stale",
                    projection,
                    mode=mode,
                    failed_step="project-removal:revalidate",
                )
            if not purge:
                blockers = _forget_blockers(catalog, registration.project_id)
                if blockers and current.registration_state == "registered":
                    raise _error(
                        "project removal blockers changed: " + ", ".join(blockers),
                        projection,
                        mode=mode,
                        failed_step="project-removal:preflight",
                    )
                step = steps[0]
                context.action(step.step_id)
                if current.registration_state == "registered":
                    catalog.forget_project(registration.project_id, _lock_held=True)
                context.complete_action(step.step_id)
                forget_retained = projection.public_decisions
                return ProjectRemovalResult(
                    project_id=registration.project_id,
                    mode="forget",
                    registration_state="forgotten",
                    completed=(decisions[0],),
                    retained=forget_retained,
                )
            if confirmation != projection.expected_confirmation:
                raise _error(
                    "exact project confirmation is required",
                    projection,
                    mode=mode,
                    failed_step="project-removal:confirmation",
                )
            completed: list[ProjectResourceDecision] = []
            retained: list[ProjectResourceDecision] = []
            remaining: list[ProjectResourceDecision] = []
            for decision, step in zip(decisions, steps, strict=True):
                context.action(step.step_id)
                if decision.action == "retain":
                    retained.append(decision)
                    context.complete_action(step.step_id)
                    continue
                adapter = adapter_set.for_decision(decision)
                if adapter is None:
                    remaining.append(decision)
                    context.complete_action(step.step_id)
                    continue
                completed.append(adapter.apply(decision, project_id=registration.project_id))
                context.complete_action(step.step_id)
            if remaining or current.registration_state == "registered":
                return ProjectRemovalResult(
                    project_id=registration.project_id,
                    mode="purge",
                    registration_state=current.registration_state,
                    completed=tuple(completed),
                    retained=tuple(retained),
                    remaining=tuple(remaining),
                )
            return ProjectRemovalResult(
                project_id=registration.project_id,
                mode="purge",
                registration_state="forgotten",
                completed=tuple(completed),
                retained=tuple(retained),
            )

    return Command.create(plan, run, steps)


def expected_project_confirmation(catalog: BackupCatalog, selector: ProjectSelector) -> str:
    """Return the exact token for CLI prompt transport without building a command."""
    registration = resolve_project_selector(
        selector,
        tuple(_row_registration(row) for row in catalog.list_projects(include_forgotten=True)),
    )
    return _projection(catalog, registration).expected_confirmation


__all__ = [
    "ProjectRemovalAdapter",
    "ProjectRemovalAdapters",
    "capture_project_removal_command",
    "expected_project_confirmation",
]
