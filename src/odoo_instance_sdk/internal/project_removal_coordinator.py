"""Project-removal command composition and the non-purge lifecycle boundary.

The coordinator owns selection, one immutable action ledger, the project
lifecycle lock, and the terminal catalogue transition.  Resource-specific
purge work is deliberately represented by sealed adapter protocols so the
runtime and data packages can consume the same captured decisions later.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    ConfigError,
    ProjectManifestNotFoundError,
    ProjectRemovalError,
)
from odoo_instance_sdk.execution import (
    Command,
    ExecutionPlan,
    JsonValue,
    SemanticPlanObservation,
)
from odoo_instance_sdk.internal.git_worktree import project_git_status_argv
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
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.project_removal import (
    ProjectRegistration,
    ProjectRemovalProjection,
    RemovalPublicationSource,
    RemovalRuntimeSource,
    build_project_removal_projection,
    removal_fingerprint,
    resolve_project_selector,
)
from odoo_instance_sdk.internal.publication_routes import read_owned_routes
from odoo_instance_sdk.internal.resource_inventory import (
    ActiveUse,
    EnvironmentResourceSource,
    OwnershipConfidence,
    ProjectResourceSource,
    VolumeResourceSource,
    collect_resource_inventory,
)
from odoo_instance_sdk.internal.sanitize import sanitize_event_message
from odoo_instance_sdk.models.database_inventory import (
    DatabaseInventoryItem,
    DatabaseInventoryResult,
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


def _row_registration(row: sqlite3.Row) -> ProjectRegistration:
    return ProjectRegistration(
        project_id=str(row["project_id"]),
        repository_root=Path(str(row["repository_root"])),
        git_common_dir=Path(str(row["git_common_dir"])),
        registration_state=cast(
            "Literal['registered', 'forgotten']",
            str(row["registration_state"]),
        ),
    )


def _environment_owner_clause(
    registration: ProjectRegistration,
    *,
    alias: str = "e",
) -> tuple[str, tuple[str, ...]]:
    """Select current and legacy rows owned by one captured registration."""
    if registration.git_common_dir is None:
        return f"{alias}.project_id = ?", (registration.project_id,)
    return (
        (
            f"({alias}.project_id = ? OR ({alias}.project_id IS NULL "
            f"AND {alias}.repository_root = ? AND {alias}.git_common_dir = ?))"
        ),
        (
            registration.project_id,
            str(registration.repository_root),
            str(registration.git_common_dir),
        ),
    )


def _environment_sources(
    catalog: BackupCatalog, registration: ProjectRegistration
) -> tuple[EnvironmentResourceSource, ...]:
    owner_clause, owner_params = _environment_owner_clause(registration)
    rows = catalog._conn.execute(
        f"SELECT * FROM environments e WHERE {owner_clause} ORDER BY id", owner_params
    ).fetchall()
    return tuple(
        EnvironmentResourceSource(
            environment_id=str(row["id"]),
            project_id=registration.project_id,
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


def _project_runtime(
    catalog: BackupCatalog, registration: ProjectRegistration
) -> tuple[RemovalRuntimeSource, ...]:
    project_id = registration.project_id
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
    owner_clause, owner_params = _environment_owner_clause(registration)
    for row in catalog._conn.execute(
        f"SELECT e.id FROM environments e WHERE {owner_clause} ORDER BY e.id", owner_params
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


def _projection(  # noqa: C901 -- source-less claim recovery shares the existing projection boundary
    catalog: BackupCatalog,
    registration: ProjectRegistration,
    *,
    database_inventory: DatabaseInventoryResult | None = None,
    volume_observation: VolumeResourceSource | None = None,
    git_status_step_id: str | None = None,
    git_status_hardened: bool = True,
) -> ProjectRemovalProjection:
    environments = _environment_sources(catalog, registration)
    from odoo_instance_sdk.internal.project_filesystem_purge import project_owned_paths
    from odoo_instance_sdk.project import ProjectConfig, managed_filestore_path

    manifest = registration.repository_root / ".odcli" / "project.toml"
    manifest_present = manifest.exists() or manifest.is_symlink()
    config_evidence_complete = True
    managed_filestore: Path | None = None
    try:
        managed_filestore = managed_filestore_path(ProjectConfig.load(registration.repository_root))
    except ProjectManifestNotFoundError:
        config_evidence_complete = not registration.repository_root.exists()
    except (ConfigError, OSError, ValueError):
        config_evidence_complete = False
    owned_paths = project_owned_paths(
        registration.repository_root, managed_filestore=managed_filestore
    )
    database_name: str | None = None
    try:
        config = ProjectConfig.load(registration.repository_root)
        database_name = config.default_source_database
    except ProjectManifestNotFoundError:
        config_evidence_complete = config_evidence_complete and not manifest_present
    except (ConfigError, OSError, ValueError):
        config_evidence_complete = False
    claim = catalog._get_postgres_cluster_for_project(
        registration.project_id,
        registration.repository_root,
        registration.git_common_dir,
    )
    active_claim = claim if claim is not None and claim.state == "active" else None
    active_cluster_id = str(active_claim.cluster_id) if active_claim is not None else None
    database_required = database_name is not None or active_claim is not None
    observed_databases = tuple(database_inventory.databases) if database_inventory else ()
    owned_databases: tuple[DatabaseInventoryItem, ...] = ()
    if database_name is None and active_claim is not None and observed_databases:
        owned_databases = tuple(
            item for item in observed_databases if item.cluster_id == str(active_claim.cluster_id)
        )
        if owned_databases:
            # A source-less project has no manifest default.  An inventory
            # already bound to the exact active claim is the durable default
            # for the projection; all returned databases remain represented
            # as separate decisions below.
            database_name = owned_databases[0].name
    if active_claim is not None and database_name is not None:
        observed_databases = tuple(
            msgspec.structs.replace(item, cluster_id=str(active_claim.cluster_id))
            if item.name == database_name and item.cluster_id is None
            else item
            for item in observed_databases
        )
    if database_name is not None and active_claim is not None and not owned_databases:
        owned_databases = tuple(
            item
            for item in observed_databases
            if item.name == database_name and item.cluster_id == str(active_claim.cluster_id)
        )
    database_observed = bool(owned_databases) and all(
        item.cluster_id == active_cluster_id for item in owned_databases
    )
    if database_required and database_name is not None and not database_observed:
        database_cluster = (
            str(active_claim.cluster_id) if active_claim is not None else "project-database"
        )
        observed_databases = (
            *observed_databases,
            DatabaseInventoryItem(
                cluster=database_cluster,
                cluster_id=str(active_claim.cluster_id) if active_claim is not None else None,
                name=database_name,
                logical_size_bytes=None,
                active_sessions=0,
                is_default=True,
                origin="unknown",
            ),
        )
    if database_inventory is not None or observed_databases:
        database_inventory = DatabaseInventoryResult(
            cluster=database_inventory.cluster if database_inventory is not None else "unknown",
            databases=observed_databases,
            tracked=database_inventory.tracked if database_inventory is not None else False,
        )
    volumes: tuple[VolumeResourceSource, ...] = ()
    volume_observed = active_claim is None
    if active_claim is not None:
        if (
            volume_observation is not None
            and volume_observation.project_id == registration.project_id
            and volume_observation.cluster_id == str(active_claim.cluster_id)
            and volume_observation.volume_name == active_claim.volume_name
        ):
            volumes = (volume_observation,)
            volume_observed = volume_observation.available and (
                volume_observation.active is False
                or (
                    volume_observation.active is True
                    and volume_observation.container_identity_verified
                )
            )
        else:
            volume_observation = VolumeResourceSource(
                project_id=registration.project_id,
                volume_name=active_claim.volume_name,
                cluster_id=str(active_claim.cluster_id),
                owned=True,
                available=False,
                usage_bytes=None,
                active=None,
                reason="live Compose volume observation unavailable during removal planning",
            )
            volumes = (volume_observation,)
            volume_observed = False
    database_measurement_complete = not database_required or database_observed
    database_inactive = not database_required or all(
        item.active_sessions == 0 for item in owned_databases
    )
    volume_inactive = active_claim is None or volume_observed
    required_observations_complete = (
        config_evidence_complete
        and not registration.repository_root.is_symlink()
        and database_measurement_complete
        and database_inactive
        and volume_observed
        and volume_inactive
    )
    project_database_names = tuple(item.name for item in owned_databases)
    if not project_database_names and database_name is not None:
        project_database_names = (database_name,)
    project_sources = tuple(
        ProjectResourceSource(
            project_id=registration.project_id,
            repository_root=registration.repository_root,
            runtime_database=name,
            database_cluster=(str(claim.cluster_id) if claim is not None else None),
            config_paths=tuple(path for kind, path in owned_paths if kind == "config"),
            secret_paths=tuple(path for kind, path in owned_paths if kind == "secret"),
            filestore_paths=tuple(path for kind, path in owned_paths if kind == "filestore"),
            primary_python_environment_path=next(
                (path for kind, path in owned_paths if kind == "python_environment"), None
            ),
            log_paths=tuple(path for kind, path in owned_paths if kind == "log"),
            temp_paths=tuple(path for kind, path in owned_paths if kind == "temporary"),
        )
        for name in (project_database_names or (None,))
    )
    inventory = collect_resource_inventory(
        catalog=catalog,
        database_inventory=database_inventory,
        database_measurement_complete=database_measurement_complete,
        database_measurement_reason=(
            None
            if database_measurement_complete
            else "project-scoped PostgreSQL observation unavailable or unproven"
        ),
        projects=project_sources,
        environments=environments,
        volumes=volumes,
        project_id=registration.project_id,
        roots=(registration.repository_root,),
    )
    return build_project_removal_projection(
        registration=registration,
        inventory=inventory,
        runtimes=_project_runtime(catalog, registration),
        publications=_publication_sources(registration.project_id),
        complete=required_observations_complete,
        git_status_step_id=git_status_step_id,
        git_status_hardened=git_status_hardened,
    )


def _planned_projection(
    catalog: BackupCatalog,
    registration: ProjectRegistration,
    *,
    database_inventory: DatabaseInventoryResult | None,
    volume_observation: VolumeResourceSource | None,
    git_status_step_id: str | None = None,
    git_status_hardened: bool = True,
) -> ProjectRemovalProjection:
    """Build a projection while preserving the no-observation test seam."""
    if database_inventory is None and volume_observation is None:
        return _projection(
            catalog,
            registration,
            git_status_step_id=git_status_step_id,
            git_status_hardened=git_status_hardened,
        )
    return _projection(
        catalog,
        registration,
        database_inventory=database_inventory,
        volume_observation=volume_observation,
        git_status_step_id=git_status_step_id,
        git_status_hardened=git_status_hardened,
    )


def _forget_resource_fingerprint(projection: ProjectRemovalProjection) -> str:
    """Fingerprint stable forget decisions, excluding mutable DB/volume evidence."""
    stable_decisions = tuple(
        decision
        for decision in projection.decisions
        if decision.resource_type not in {"database", "volume"}
    )
    return removal_fingerprint(
        projection.project_id,
        projection.registration_state,
        projection.repository_root,
        stable_decisions,
        complete=True,
    )


def _forget_blockers(catalog: BackupCatalog, registration: ProjectRegistration) -> tuple[str, ...]:
    project_id = registration.project_id
    blockers: set[str] = set()
    if catalog.get_runtime("project", project_id) is not None:
        blockers.add(f"runtime:project:{project_id}")
    owner_clause, owner_params = _environment_owner_clause(registration)
    for row in catalog._conn.execute(
        f"SELECT e.id FROM environments e WHERE {owner_clause} ORDER BY e.id", owner_params
    ):
        environment_id = str(row["id"])
        if catalog.get_runtime("environment", environment_id) is not None:
            blockers.add(f"runtime:environment:{environment_id}")
    for row in catalog._conn.execute(
        f"SELECT e.id, e.state FROM environments e WHERE {owner_clause} ORDER BY e.id",
        owner_params,
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
    decisions: tuple[ProjectResourceDecision, ...] | None = None,
    completed: tuple[ProjectResourceDecision, ...] = (),
    retained: tuple[ProjectResourceDecision, ...] | None = None,
    remaining: tuple[ProjectResourceDecision, ...] | None = None,
) -> ProjectRemovalErrorDetails:
    public = projection.public_decisions if decisions is None else decisions
    retained_values = (
        tuple(item for item in public if item.action == "retain") if retained is None else retained
    )
    remaining_values = (
        tuple(item for item in public if item.action != "retain")
        if remaining is None
        else remaining
    )
    return ProjectRemovalErrorDetails(
        project_id=projection.project_id,
        mode=mode,
        registration_state=projection.registration_state,
        completed=completed,
        retained=retained_values,
        remaining=remaining_values,
        failed_step=failed_step,
        failed_identity=failed_step.removeprefix("project-removal:") or None,
    )


def _error(
    message: str,
    projection: ProjectRemovalProjection,
    *,
    mode: Literal["forget", "purge"],
    failed_step: str,
    decisions: tuple[ProjectResourceDecision, ...] | None = None,
    completed: tuple[ProjectResourceDecision, ...] = (),
    retained: tuple[ProjectResourceDecision, ...] | None = None,
    remaining: tuple[ProjectResourceDecision, ...] | None = None,
) -> ProjectRemovalError:
    limit = 100
    public = projection.public_decisions if decisions is None else decisions
    completed_total = len(completed)
    if retained is None:
        retained = tuple(item for item in public if item.action == "retain")
    if remaining is None:
        remaining = tuple(item for item in public if item.action != "retain")
    retained_total = len(retained)
    remaining_total = len(remaining)
    completed = completed[:limit]
    retained = retained[:limit]
    remaining = remaining[:limit]
    details = _error_details(
        projection,
        mode=mode,
        failed_step=failed_step,
        decisions=decisions,
        completed=completed,
        retained=retained,
        remaining=remaining,
    )
    builtins = cast("dict[str, JsonValue]", msgspec.to_builtins(details))
    builtins["completed_total"] = completed_total
    builtins["retained_total"] = retained_total
    builtins["remaining_total"] = remaining_total
    builtins["truncated"] = any(
        value > limit for value in (completed_total, retained_total, remaining_total)
    )
    safe_message = sanitize_event_message(message) or "project removal failed"
    return ProjectRemovalError(safe_message, details={"removal": builtins})


def _catalogue_forget_command(
    catalog: BackupCatalog,
    decision: ProjectResourceDecision,
    *,
    project_id: str,
) -> Command[ProjectResourceDecision]:
    action = _action_step(decision)

    def run(context: RunContext[ProjectResourceDecision]) -> ProjectResourceDecision:
        context.action(action.step_id)
        row = catalog.get_project_for_lifecycle(project_id)
        if row is None:
            raise ProjectRemovalError("project registration disappeared")
        if str(row["registration_state"]) == "registered":
            catalog.forget_project(project_id, _lock_held=True)
        context.complete_action(action.step_id)
        return decision

    return Command.create(
        ExecutionPlan(steps=(action.public_projection(),)),
        run,
        (action,),
    )


def capture_project_removal_command(  # noqa: C901
    catalog: BackupCatalog,
    selector: ProjectSelector,
    *,
    purge: bool = False,
    confirmation: str | None = None,
    adapters: ProjectRemovalAdapters | None = None,
    database_inventory: DatabaseInventoryResult | None = None,
    volume_observation: VolumeResourceSource | None = None,
) -> Command[ProjectRemovalResult]:
    """Capture one project-removal plan; execution revalidates it under lock."""
    registrations = tuple(
        _row_registration(row) for row in catalog.list_projects(include_forgotten=True)
    )
    registration = resolve_project_selector(selector, registrations)
    git_status_step_id = (
        f"project-removal:git-status:{registration.project_id}"
        if (registration.repository_root / ".git" / "HEAD").is_file()
        else None
    )
    source_status_step = (
        PreparedStep(
            step_id=git_status_step_id,
            argv=project_git_status_argv(registration.repository_root),
            read_only=True,
        )
        if git_status_step_id is not None
        else None
    )
    projection = _planned_projection(
        catalog,
        registration,
        database_inventory=database_inventory,
        volume_observation=volume_observation,
        git_status_step_id=git_status_step_id,
        git_status_hardened=True,
    )
    mode: Literal["forget", "purge"] = "purge" if purge else "forget"
    decisions: tuple[ProjectResourceDecision, ...]
    if not purge:
        blockers = _forget_blockers(catalog, registration)
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
        projected_decisions = projection.decisions
        if not projection.complete:
            projected_decisions = tuple(
                replace(
                    decision,
                    action="retain",
                    reason="observation_incomplete",
                )
                if decision.action != "retain"
                else decision
                for decision in projected_decisions
            )
        decisions = (
            *tuple(
                decision.public()
                for decision in projected_decisions
                if not (
                    decision.action == "delete"
                    and decision.resource_type in {"worktree", "python_environment"}
                    and any(
                        relationship.startswith("environment:")
                        for relationship in decision.relationships
                    )
                )
            ),
            *(
                (
                    ProjectResourceDecision(
                        identity=f"project-forget:{registration.project_id}",
                        resource_type="project",
                        action="forget",
                        reason="terminal_registration_transition",
                        target="<catalogue>",
                    ),
                )
                if projection.complete
                else ()
            ),
        )
    adapter_set = adapters or _NoPurgeAdapters()
    captured_children: dict[str, PreparedCommand[ProjectResourceDecision]] = {}
    flattened_steps: list[PreparedAction | PreparedStep] = []
    child_executors: dict[str, ProcessExecutor] = {}
    if source_status_step is not None:
        flattened_steps.append(source_status_step)
        child_executors[source_status_step.step_id] = SubprocessExecutor()
    for decision in decisions:
        child: Command[ProjectResourceDecision] | None
        if not purge or decision.action == "retain":
            flattened_steps.append(_action_step(decision))
            continue
        if decision.action == "forget":
            child = _catalogue_forget_command(catalog, decision, project_id=registration.project_id)
            prepared = child._prepared()
            captured_children[decision.identity] = prepared
            flattened_steps.extend(prepared.steps)
            continue
        adapter = adapter_set.for_decision(decision)
        if adapter is None:
            flattened_steps.append(_action_step(decision))
            continue
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
        fingerprint=(
            _forget_resource_fingerprint(projection) if not purge else projection.fingerprint
        ),
    )
    captured_forget_fingerprint = _forget_resource_fingerprint(projection) if not purge else None
    confirmation_value = [confirmation]

    def run(context: RunContext[ProjectRemovalResult]) -> ProjectRemovalResult:  # noqa: C901
        if purge and confirmation_value[0] != projection.expected_confirmation:
            raise _error(
                "exact project confirmation is required",
                projection,
                mode=mode,
                failed_step="project-removal:confirmation",
                decisions=decisions,
            )
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
            if (
                current.repository_root.resolve() != registration.repository_root.resolve()
                or current.git_common_dir is None
                or registration.git_common_dir is None
                or current.git_common_dir.resolve() != registration.git_common_dir.resolve()
            ):
                raise _error(
                    "project registration identity changed",
                    projection,
                    mode=mode,
                    failed_step="project-removal:revalidate",
                )
            if current.registration_state != registration.registration_state:
                raise _error(
                    "project registration state changed",
                    projection,
                    mode=mode,
                    failed_step="project-removal:revalidate",
                )
            if not purge:
                current_projection = _planned_projection(
                    catalog,
                    current,
                    database_inventory=database_inventory,
                    volume_observation=volume_observation,
                    git_status_step_id=git_status_step_id,
                    git_status_hardened=git_status_step_id is not None,
                )
                assert captured_forget_fingerprint is not None
                if _forget_resource_fingerprint(current_projection) != captured_forget_fingerprint:
                    raise _error(
                        "project removal plan is stale",
                        projection,
                        mode=mode,
                        failed_step="project-removal:revalidate",
                    )
                blockers = _forget_blockers(catalog, current)
                if blockers and current.registration_state == "registered":
                    raise _error(
                        "project removal blockers changed: " + ", ".join(blockers),
                        projection,
                        mode=mode,
                        failed_step="project-removal:preflight",
                    )
                step = next(step for step in steps if isinstance(step, PreparedAction))
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
            current_projection = _planned_projection(
                catalog,
                current,
                database_inventory=database_inventory,
                volume_observation=volume_observation,
                git_status_step_id=git_status_step_id,
                git_status_hardened=git_status_step_id is not None,
            )
            if not projection.complete:
                raise _error(
                    "required database or Compose observation is unavailable or unproven",
                    projection,
                    mode=mode,
                    failed_step="project-removal:observations",
                    decisions=decisions,
                )
            if not current_projection.complete:
                raise _error(
                    "required database or Compose observation is unavailable or unproven",
                    current_projection,
                    mode=mode,
                    failed_step="project-removal:observations",
                    decisions=current_projection.public_decisions,
                )
            if current_projection.fingerprint != projection.fingerprint:
                raise _error(
                    "project removal plan is stale",
                    projection,
                    mode=mode,
                    failed_step="project-removal:revalidate",
                )
            completed: list[ProjectResourceDecision] = []
            retained: list[ProjectResourceDecision] = []
            remaining: list[ProjectResourceDecision] = []
            for decision in decisions:
                if decision.action == "retain":
                    step = _action_step(decision)
                    context.action(step.step_id)
                    context.complete_action(step.step_id)
                    retained.append(decision)
                    continue
                try:
                    child = captured_children.get(decision.identity)
                    if child is None:
                        step = _action_step(decision)
                        remaining.extend(
                            item
                            for item in decisions
                            if item.action != "retain"
                            and item.identity not in {value.identity for value in completed}
                        )
                        failure = _error(
                            "no captured adapter is available for mutating project resource",
                            projection,
                            mode=mode,
                            failed_step=step.step_id,
                            decisions=decisions,
                            completed=tuple(completed),
                            retained=tuple(retained),
                            remaining=tuple(remaining),
                        )
                        raise failure  # noqa: TRY301 -- preserve the structured retry ledger
                    callback = cast(
                        "Callable[[RunContext[ProjectRemovalResult]], ProjectResourceDecision]",
                        child.callback,
                    )
                    completed.append(callback(context))
                except ProjectRemovalError as exc:
                    remaining.extend(
                        item
                        for item in decisions
                        if item.action != "retain"
                        and item.identity not in {value.identity for value in completed}
                    )
                    raise _error(
                        f"project removal step failed: {sanitize_event_message(str(exc)) or 'operation failed'}",
                        projection,
                        mode=mode,
                        failed_step=decision.identity,
                        decisions=decisions,
                        completed=tuple(completed),
                        retained=tuple(retained),
                        remaining=tuple(remaining),
                    ) from exc
                except Exception as exc:
                    remaining.extend(
                        item
                        for item in decisions
                        if item.action != "retain"
                        and item.identity not in {value.identity for value in completed}
                    )
                    raise _error(
                        f"project removal step failed: {exc}",
                        projection,
                        mode=mode,
                        failed_step=decision.identity,
                        decisions=decisions,
                        completed=tuple(completed),
                        retained=tuple(retained),
                        remaining=tuple(remaining),
                    ) from exc
            if remaining:
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

    executor: ProcessExecutor | None = (
        _ChildExecutor(child_executors)
        if child_executors
        else SubprocessExecutor()
        if source_status_step is not None
        else None
    )
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
