"""Project-removal command composition and the non-purge lifecycle boundary.

The coordinator owns selection, one immutable action ledger, the project
lifecycle lock, and the terminal catalogue transition.  Resource-specific
purge work is deliberately represented by sealed adapter protocols so the
runtime and data packages can consume the same captured decisions later.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

import msgspec

from odoo_instance_sdk.exceptions import ProjectRemovalError
from odoo_instance_sdk.execution import (
    Command,
    ExecutionPlan,
    JsonValue,
    SemanticPlanObservation,
)
from odoo_instance_sdk.internal.locks import exclusive_lock, project_manifest_lock_path
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedCommand,
    PreparedStep,
    ProcessExecutor,
    ProcessHandle,
    ProcessResultLike,
    RunContext,
    StepObserver,
)
from odoo_instance_sdk.internal.project_removal import (
    ProjectRegistration,
    ProjectRemovalProjection,
    RemovalPublicationSource,
    RemovalRuntimeSource,
    build_project_removal_projection,
    resolve_project_selector,
)
from odoo_instance_sdk.internal.publication_routes import read_owned_routes
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

    def capture(
        self,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
    ) -> Command[ProjectResourceDecision] | None: ...


class PublicationSnapshotPurgeAdapter(Protocol):
    """Compose publication removal from the immutable projected route."""

    def capture(
        self,
        decision: ProjectResourceDecision,
        *,
        project_id: str,
        route_snapshot: PublicationRoute,
    ) -> Command[ProjectResourceDecision]: ...


class ProjectRemovalAdapters(Protocol):
    """Optional family adapters consumed by later purge work packages."""

    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None: ...


class _NoPurgeAdapters:
    def for_decision(self, decision: ProjectResourceDecision) -> ProjectRemovalAdapter | None:
        del decision
        return None


class _ChildExecutor:
    """Dispatch flattened process steps to their captured child executor."""

    def __init__(self, executors: dict[str, ProcessExecutor]) -> None:
        self._executors = executors

    def _executor(self, step_id: str) -> ProcessExecutor:
        try:
            return self._executors[step_id]
        except KeyError as exc:
            raise RuntimeError(f"no captured executor for project-removal step {step_id}") from exc

    def execute(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessResultLike:
        return self._executor(step.step_id).execute(
            step, observer=observer, observe_output=observe_output
        )

    def spawn(
        self,
        step: PreparedStep,
        *,
        observer: StepObserver | None = None,
        observe_output: bool = False,
    ) -> ProcessHandle:
        return self._executor(step.step_id).spawn(
            step, observer=observer, observe_output=observe_output
        )


_CONFIRMATION_VALUES: dict[int, list[str | None]] = {}
_CONFIRMATION_TOKENS: dict[int, str] = {}


def expected_project_confirmation_for_command(command: Command[ProjectRemovalResult]) -> str:
    """Read the token captured by one command without reconstructing its projection."""
    try:
        return _CONFIRMATION_TOKENS[id(command)]
    except KeyError as exc:
        raise ValueError("project removal command has no captured confirmation token") from exc


def set_project_confirmation(command: Command[ProjectRemovalResult], confirmation: str) -> None:
    """Transport an interactive token to an already captured command."""
    try:
        _CONFIRMATION_VALUES[id(command)][0] = confirmation
    except KeyError as exc:
        raise ValueError("project removal command has no confirmation transport") from exc


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
    sources: list[RemovalRuntimeSource] = []
    if catalog.get_runtime("project", project_id) is not None:
        sources.append(
            RemovalRuntimeSource(
                project_id=project_id,
                identity=f"runtime:project:{project_id}",
                active_use=ActiveUse.ACTIVE,
                ownership=OwnershipConfidence.PROVEN,
                stale=False,
            )
        )
    for row in catalog._conn.execute(
        "SELECT id FROM environments WHERE project_id = ? ORDER BY id", (project_id,)
    ):
        environment_id = str(row["id"])
        if catalog.get_runtime("environment", environment_id) is not None:
            sources.append(
                RemovalRuntimeSource(
                    project_id=project_id,
                    identity=f"runtime:environment:{environment_id}",
                    target="environment runtime",
                    active_use=ActiveUse.ACTIVE,
                    ownership=OwnershipConfidence.PROVEN,
                    stale=False,
                )
            )
    return tuple(sources)


def _publication_sources(project_id: str) -> tuple[RemovalPublicationSource, ...]:
    sources: list[RemovalPublicationSource] = []
    for route in read_owned_routes():
        belongs = route.project_id == project_id or (
            route.owner_kind == "project" and route.owner_id == project_id
        )
        if belongs:
            sources.append(
                RemovalPublicationSource(
                    project_id=project_id,
                    identity=f"publication:{route.owner_kind}:{route.owner_id}",
                    target="publication route",
                    active_use=ActiveUse.ACTIVE,
                    ownership=OwnershipConfidence.PROVEN,
                )
            )
    return tuple(sources)


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
        publications=_publication_sources(registration.project_id),
    )


def _forget_blockers(catalog: BackupCatalog, project_id: str) -> tuple[str, ...]:
    blockers: set[str] = set()
    if catalog.get_runtime("project", project_id) is not None:
        blockers.add(f"runtime:project:{project_id}")
    for row in catalog._conn.execute(
        "SELECT id FROM environments WHERE project_id = ? ORDER BY id", (project_id,)
    ):
        environment_id = str(row["id"])
        if catalog.get_runtime("environment", environment_id) is not None:
            blockers.add(f"runtime:environment:{environment_id}")
    for row in catalog._conn.execute(
        "SELECT id, state FROM environments WHERE project_id = ? ORDER BY id", (project_id,)
    ):
        if str(row["state"]) != "removed":
            blockers.add(f"environment:{row['id']}")
    blockers.update(source.identity for source in _publication_sources(project_id))
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
    adapter_set = adapters or _NoPurgeAdapters()
    captured_children: dict[str, PreparedCommand[ProjectResourceDecision]] = {}
    flattened_steps: list[PreparedAction | PreparedStep] = []
    child_executors: dict[str, ProcessExecutor] = {}
    for decision in decisions:
        if not purge or decision.action == "retain":
            flattened_steps.append(_action_step(decision))
            continue
        adapter = adapter_set.for_decision(decision)
        if adapter is None:
            flattened_steps.append(_action_step(decision))
            continue
        if decision.resource_type == "publication":
            private_decision = captured_projection.get(decision.identity)
            route_snapshot = (
                private_decision.publication_route if private_decision is not None else None
            )
            if route_snapshot is None:
                raise _error(
                    "project publication snapshot is unavailable",
                    projection,
                    mode=mode,
                    failed_step="project-removal:revalidate",
                    decisions=decisions,
                )
            child = cast("PublicationSnapshotPurgeAdapter", adapter).capture(
                decision,
                project_id=registration.project_id,
                route_snapshot=route_snapshot,
            )
        else:
            child = adapter.capture(decision, project_id=registration.project_id)
        if child is None:
            flattened_steps.append(_action_step(decision))
            continue
        prepared = child._prepared()
        captured_children[decision.identity] = prepared
        flattened_steps.extend(prepared.steps)
        for step in prepared.steps:
            if isinstance(step, PreparedStep):
                child_executors[step.step_id] = prepared.executor
    steps = tuple(flattened_steps)
    plan = ExecutionPlan(
        steps=tuple(step.public_projection() for step in steps),
        observations=(
            SemanticPlanObservation(
                kind="semantic",
                goal=f"{mode} project {registration.project_id}",
                targets=(projection.expected_confirmation,),
                mutations=tuple(decision.action for decision in decisions),
            ),
        ),
        warnings=(),
        fingerprint=projection.fingerprint,
    )
    confirmation_value = [confirmation]

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
            if confirmation_value[0] != projection.expected_confirmation:
                raise _error(
                    "exact project confirmation is required",
                    projection,
                    mode=mode,
                    failed_step="project-removal:confirmation",
                )
            completed: list[ProjectResourceDecision] = []
            retained: list[ProjectResourceDecision] = []
            remaining: list[ProjectResourceDecision] = []
            for decision in decisions:
                if decision.action == "retain":
                    retained.append(decision)
                    continue
                child = captured_children.get(decision.identity)
                if child is None:
                    step = _action_step(decision)
                    context.action(step.step_id)
                    remaining.append(decision)
                    context.complete_action(step.step_id)
                    continue
                callback = cast(
                    "Callable[[RunContext[ProjectRemovalResult]], ProjectResourceDecision]",
                    child.callback,
                )
                completed.append(callback(context))
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

    executor: ProcessExecutor | None = _ChildExecutor(child_executors) if child_executors else None
    command = Command.create(plan, run, steps, executor=executor)
    _CONFIRMATION_VALUES[id(command)] = confirmation_value
    _CONFIRMATION_TOKENS[id(command)] = projection.expected_confirmation
    return command


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
