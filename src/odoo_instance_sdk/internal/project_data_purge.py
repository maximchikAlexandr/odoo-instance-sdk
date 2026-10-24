"""Ownership-safe data and Compose cleanup adapters for project removal.

The project-removal coordinator owns ordering and the public ledger.  This
module owns only the data providers it can prove: the existing guarded
PostgreSQL drop command, the existing backup deletion command, contained
filestore cleanup, and an exact SDK-owned Compose container/volume pair.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar, cast

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.execution import Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.locks import exclusive_lock, postgres_cluster_lock_path
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessResult,
    ProcessResultLike,
    RunContext,
    SubprocessExecutor,
    prepared_command,
)
from odoo_instance_sdk.models.project_removal import ProjectResourceDecision

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.internal.postgres_compose import ComposeRunner
    from odoo_instance_sdk.models.backup import Backup
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

_ChildResult = TypeVar("_ChildResult")


def _result_text(result: ProcessResultLike) -> str:
    if not isinstance(result, ProcessResult):
        raise ConfigError("project cleanup process returned no result")
    value = result.stdout
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def _docker_not_found(result: ProcessResult, *, label: str) -> bool:
    """Return true only for Docker's verified object-absent response."""
    if result.returncode == 0:
        return False
    error = _result_text(result).lower()
    stderr = result.stderr
    if isinstance(stderr, bytes):
        error += "\n" + stderr.decode(errors="replace").lower()
    elif stderr:
        error += "\n" + str(stderr).lower()
    # Docker's inspect API uses these stable phrases for an absent object.
    # Generic non-zero results (daemon, permission, transport, and malformed
    # invocations) are intentionally not treated as an idempotent absence.
    object_name = label.lower()
    return any(
        marker in error
        for marker in (
            "no such object",
            "no such container",
            "no such volume",
            f"{object_name} not found",
        )
    )


def _json_object(result: ProcessResultLike, *, label: str) -> Mapping[str, JsonValue] | None:
    if not isinstance(result, ProcessResult):
        raise ConfigError(f"{label} inspection returned no result")
    if result.returncode != 0:
        if _docker_not_found(result, label=label):
            return None
        raise ConfigError(f"{label} inspection failed; object absence is unproven")
    try:
        value: JsonValue = json.loads(_result_text(result))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{label} inspection returned invalid identity") from exc
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} inspection returned invalid identity")
    return cast("Mapping[str, JsonValue]", value)


def _require_absent(result: ProcessResultLike, *, label: str) -> None:
    """Require a postcondition to be either a successful not-found result or fail."""
    if not isinstance(result, ProcessResult):
        raise ConfigError(f"{label} postcondition returned no result")
    if result.returncode == 0:
        raise ConfigError(f"Compose {label} removal postcondition failed")
    if not _docker_not_found(result, label=label):
        raise ConfigError(f"{label} postcondition failed; object absence is unproven")


def _adapt_child(
    child: Command[_ChildResult],
    decision: ProjectResourceDecision,
) -> Command[ProjectResourceDecision]:
    """Keep a captured child plan/executor, changing only its result type."""
    prepared = child._prepared()

    def run(context: RunContext[ProjectResourceDecision]) -> ProjectResourceDecision:
        prepared.callback(cast("RunContext[_ChildResult]", context))
        return decision

    return Command.from_prepared(
        child.plan,
        prepared_command(run, prepared.steps, executor=prepared.executor),
    )


@dataclass(frozen=True, slots=True)
class DatabasePurgeTarget:
    """Exact database identity captured from the project inventory."""

    identity: str
    project_id: str
    project_root: Path
    database_name: str
    database_oid: int | None = None
    instance: OdooInstance | None = None
    project_config: ProjectConfig | None = None


@dataclass(frozen=True, slots=True)
class FilestorePurgeTarget:
    """A database-owned filestore with an explicit canonical containment root."""

    identity: str
    project_id: str
    path: Path
    allowed_root: Path
    ownership_check: Callable[[], bool] | None = None
    database_provenance_check: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class ComposeClusterPurgeTarget:
    """Exact Compose identities and the evidence required before destruction."""

    identity: str
    project_id: str
    cluster_id: str
    compose_project: str
    container_name: str
    volume_name: str
    claim_project_id: str | None = None
    dependencies: tuple[str, ...] = ()
    dependency_check: Callable[[], bool] | None = None
    compose_file: Path | None = None
    runner: ComposeRunner | None = field(default=None, repr=False, compare=False)


class DatabaseCommandFactory(Protocol):
    def __call__(self, target: DatabasePurgeTarget, /) -> Command[JsonValue]: ...


class BackupCommandFactory(Protocol):
    def __call__(self, backup: Backup, /) -> Command[JsonValue]: ...


def _delete_contained_filestore(target: FilestorePurgeTarget) -> None:
    path = target.path
    root = target.allowed_root.resolve(strict=False)
    checks = (target.ownership_check, target.database_provenance_check)
    if not any(check is not None for check in checks):
        raise ConfigError("filestore database provenance is unavailable")
    for check in checks:
        if check is not None and not check():
            raise ConfigError("filestore database provenance changed before deletion")
    if path.is_symlink() or path.resolve(strict=False).parent != root:
        raise ConfigError("filestore path is outside its proven containment root")
    if not path.exists():
        return
    if not path.is_dir():
        raise ConfigError("proven filestore target is not a directory")
    shutil.rmtree(path)
    if path.exists() or path.is_symlink():
        raise ConfigError("filestore deletion postcondition failed")


class FilestorePurgeAdapter:
    def __init__(self, targets: Mapping[str, FilestorePurgeTarget]) -> None:
        self._targets = dict(targets)

    def capture(
        self, decision: ProjectResourceDecision, *, project_id: str
    ) -> Command[ProjectResourceDecision] | None:
        target = self._targets.get(decision.identity)
        if target is None or target.project_id != project_id:
            return None
        if target.ownership_check is None and target.database_provenance_check is None:
            return None
        action = PreparedAction(
            step_id=f"project-removal:filestore:{decision.identity}",
            action="delete-filestore",
            description="Delete one proven contained project filestore",
            details={"identity": decision.identity},
            mutating=True,
        )

        def run(context: RunContext[ProjectResourceDecision]) -> ProjectResourceDecision:
            context.action(action.step_id)
            _delete_contained_filestore(target)
            context.complete_action(action.step_id)
            return decision

        return Command.create(
            ExecutionPlan(steps=(action.public_projection(),)),
            run,
            (action,),
        )


class DatabasePurgeAdapter:
    """Compose the existing guarded database/filestore deletion command."""

    def __init__(
        self,
        client: OdooClient,
        targets: Mapping[str, DatabasePurgeTarget],
        *,
        command_factory: DatabaseCommandFactory | None = None,
        executor: ProcessExecutor | None = None,
    ) -> None:
        self._client = client
        self._targets = dict(targets)
        self._command_factory = command_factory
        self._executor = executor

    def _capture_database(self, target: DatabasePurgeTarget) -> Command[JsonValue]:
        if self._command_factory is not None:
            return self._command_factory(target)
        from odoo_instance_sdk.internal.pg.drop import build_database_drop_command
        from odoo_instance_sdk.project import ProjectConfig

        instance = target.instance
        project: ProjectConfig
        if instance is None:
            project = target.project_config or ProjectConfig.load(target.project_root)
            instance = self._client.instance.from_project(project)
        else:
            project = target.project_config or ProjectConfig.load(target.project_root)
        return cast(
            "Command[JsonValue]",
            build_database_drop_command(
                instance,
                target.project_root,
                target.database_name,
                expected_database_oid=target.database_oid,
                project=project,
                force_default=True,
                idempotent_absent=True,
                defer_planning=True,
                command_origin="project-removal",
                step_prefix=f"project-removal:{target.identity}:",
                executor=self._executor,
            ),
        )

    def capture(
        self, decision: ProjectResourceDecision, *, project_id: str
    ) -> Command[ProjectResourceDecision] | None:
        target = self._targets.get(decision.identity)
        if target is None or target.project_id != project_id:
            return None
        return _adapt_child(self._capture_database(target), decision)


class BackupPurgeAdapter:
    """Delete only a current, exclusively project-owned backup through BackupResource."""

    def __init__(
        self,
        client: OdooClient,
        catalog: BackupCatalog,
        *,
        command_factory: BackupCommandFactory | None = None,
        executor: ProcessExecutor | None = None,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._command_factory = command_factory
        self._executor = executor

    def capture(
        self, decision: ProjectResourceDecision, *, project_id: str
    ) -> Command[ProjectResourceDecision] | None:
        if not decision.identity.startswith("backup:"):
            return None
        backup_id = decision.identity.removeprefix("backup:")
        row = self._catalog.get_by_id(backup_id)
        if (
            row is None
            or row["project_id"] != project_id
            or row["state"] != "available"
            or self._catalog.deletion_protection_reason(backup_id) is not None
        ):
            return None
        projection = self._catalog._resolve_backup_projection(backup_id)
        child: Command[JsonValue]
        if self._command_factory is not None:
            child = self._command_factory(projection.backup)
        else:
            child = cast(
                "Command[JsonValue]",
                self._client.backups.delete_command(
                    projection.backup,
                    executor=self._executor,
                ),
            )
        return _adapt_child(child, decision)


def _identity_labels(
    value: Mapping[str, JsonValue], *, container: bool, target: ComposeClusterPurgeTarget
) -> bool:
    claim_project_id = target.claim_project_id or target.project_id
    if container:
        config = value.get("Config")
        labels = config.get("Labels") if isinstance(config, Mapping) else None
        mounts = value.get("Mounts")
        return (
            value.get("Name") in {f"/{target.container_name}", target.container_name}
            and isinstance(labels, Mapping)
            and labels.get("com.docker.compose.project") == target.compose_project
            and labels.get("io.odoo-instance-sdk.cluster-id") == target.cluster_id
            and labels.get("io.odoo-instance-sdk.project-id") == claim_project_id
            and isinstance(mounts, list)
            and any(
                isinstance(mount, Mapping)
                and mount.get("Type") == "volume"
                and mount.get("Name") == target.volume_name
                for mount in mounts
            )
        )
    labels = value.get("Labels")
    return (
        value.get("Name") == target.volume_name
        and isinstance(labels, Mapping)
        and labels.get("com.docker.compose.project") == target.compose_project
        and labels.get("io.odoo-instance-sdk.cluster-id") == target.cluster_id
        and labels.get("io.odoo-instance-sdk.project-id") == claim_project_id
    )


class ComposeClusterPurgeAdapter:
    """Destroy exactly one stopped, SDK-labelled Compose container and volume."""

    def __init__(
        self,
        catalog: BackupCatalog,
        targets: Mapping[str, ComposeClusterPurgeTarget],
        *,
        executor: ProcessExecutor | None = None,
    ) -> None:
        self._catalog = catalog
        self._targets = dict(targets)
        self._executor = executor or SubprocessExecutor()

    def _claim_matches(self, target: ComposeClusterPurgeTarget) -> bool:
        claim_project_id = target.claim_project_id or target.project_id
        claim = self._catalog._get_postgres_cluster(claim_project_id)
        return bool(
            claim is not None
            and claim.state == "active"
            and str(claim.cluster_id) == target.cluster_id
            and claim.project_id == claim_project_id
            and claim.compose_project == target.compose_project
            and claim.volume_name == target.volume_name
        )

    def capture(  # noqa: C901 -- one explicit guarded Compose lifecycle
        self, decision: ProjectResourceDecision, *, project_id: str
    ) -> Command[ProjectResourceDecision] | None:
        target = self._targets.get(decision.identity)
        if target is None or target.project_id != project_id:
            return None
        if not self._claim_matches(target):
            return None
        if target.dependency_check is None:
            return None
        if target.dependencies or target.dependency_check():
            return None

        prefix = f"project-removal:compose:{target.identity}"
        volume_inspect = PreparedStep(
            step_id=f"{prefix}:inspect-volume",
            argv=("docker", "volume", "inspect", "--format", "{{json .}}", target.volume_name),
            read_only=True,
            text=True,
        )
        container_inspect = PreparedStep(
            step_id=f"{prefix}:inspect-container",
            argv=("docker", "inspect", "--format", "{{json .}}", target.container_name),
            read_only=True,
            text=True,
        )
        stop = PreparedStep(
            step_id=f"{prefix}:stop-container",
            argv=("docker", "stop", target.container_name),
            mutating=True,
            text=True,
        )
        remove_container = PreparedStep(
            step_id=f"{prefix}:remove-container",
            argv=("docker", "rm", "--force", target.container_name),
            mutating=True,
            text=True,
        )
        remove_volume = PreparedStep(
            step_id=f"{prefix}:remove-volume",
            argv=("docker", "volume", "rm", target.volume_name),
            mutating=True,
            text=True,
        )
        verify_volume = PreparedStep(
            step_id=f"{prefix}:verify-volume",
            argv=("docker", "volume", "inspect", "--format", "{{json .}}", target.volume_name),
            read_only=True,
            text=True,
        )
        verify_container = PreparedStep(
            step_id=f"{prefix}:verify-container",
            argv=("docker", "inspect", "--format", "{{json .}}", target.container_name),
            read_only=True,
            text=True,
        )
        steps = (
            volume_inspect,
            container_inspect,
            stop,
            remove_container,
            remove_volume,
            verify_volume,
            verify_container,
        )
        action = PreparedAction(
            step_id=f"{prefix}:destroy",
            action="destroy-compose-cluster",
            description="Destroy one exact SDK-owned Compose container and volume",
            details={"identity": target.identity},
            mutating=True,
        )
        prepared_steps = (action, *steps)

        def skip(context: RunContext[ProjectResourceDecision], step: PreparedStep) -> None:
            if context.planned(step.step_id) and not context.consumed(step.step_id):
                context.skip(step.step_id)

        def run(  # noqa: C901 -- guard, effect, and postcondition are one transaction
            context: RunContext[ProjectResourceDecision],
        ) -> ProjectResourceDecision:
            cluster_project_id = target.claim_project_id or target.project_id
            with exclusive_lock(postgres_cluster_lock_path(cluster_project_id)):
                context.action(action.step_id)
                if not self._claim_matches(target):
                    raise ConfigError("Compose cluster claim changed before destruction")
                if target.dependency_check is None:
                    raise ConfigError("Compose cluster exclusive-use evidence is unavailable")
                if target.dependency_check():
                    raise ConfigError("Compose cluster has a retained or active dependency")
                volume = _json_object(context.process(volume_inspect.step_id), label="volume")
                container = _json_object(
                    context.process(container_inspect.step_id), label="container"
                )
                if volume is not None and not _identity_labels(
                    volume, container=False, target=target
                ):
                    raise ConfigError("Compose volume labels do not match the active project claim")
                if container is not None and not _identity_labels(
                    container, container=True, target=target
                ):
                    raise ConfigError(
                        "Compose container labels or volume mount do not match the claim"
                    )
                # Recheck immediately after both live Docker identities are known;
                # the capture-time and pre-inspection checks are not sufficient to
                # authorize a mutation if a dependent resource appeared meanwhile.
                if not self._claim_matches(target):
                    raise ConfigError("Compose cluster claim changed before destruction")
                if target.dependency_check():
                    raise ConfigError("Compose cluster has a retained or active dependency")
                if container is not None:
                    stop_result = context.process(stop.step_id)
                    if isinstance(stop_result, ProcessResult) and stop_result.returncode != 0:
                        raise ConfigError("Compose container stop failed")
                    remove_result = context.process(remove_container.step_id)
                    if isinstance(remove_result, ProcessResult) and remove_result.returncode != 0:
                        raise ConfigError("Compose container removal failed")
                else:
                    skip(context, stop)
                    skip(context, remove_container)
                if volume is not None:
                    remove_result = context.process(remove_volume.step_id)
                    if isinstance(remove_result, ProcessResult) and remove_result.returncode != 0:
                        raise ConfigError("Compose volume removal failed")
                else:
                    skip(context, remove_volume)
                verified_volume = context.process(verify_volume.step_id)
                verified_container = context.process(verify_container.step_id)
                _require_absent(verified_volume, label="volume")
                _require_absent(verified_container, label="container")
                if not self._catalog._remove_postgres_cluster_claim(
                    target.cluster_id,
                    target.claim_project_id or target.project_id,
                    target.compose_project,
                    target.volume_name,
                ):
                    raise ConfigError("Compose cluster claim changed after resource removal")
                context.complete_action(action.step_id)
                return decision

        return Command.from_prepared(
            ExecutionPlan(steps=tuple(step.public_projection() for step in prepared_steps)),
            prepared_command(run, prepared_steps, executor=self._executor),
        )


@dataclass(slots=True)
class ProjectDataPurgeAdapters:
    """Fixed coordinator adapter set for databases, backups, filestores, and Compose."""

    database: DatabasePurgeAdapter | None = None
    backup: BackupPurgeAdapter | None = None
    filestore: FilestorePurgeAdapter | None = None
    compose: ComposeClusterPurgeAdapter | None = None

    def for_decision(
        self, decision: ProjectResourceDecision
    ) -> (
        DatabasePurgeAdapter
        | BackupPurgeAdapter
        | FilestorePurgeAdapter
        | ComposeClusterPurgeAdapter
        | None
    ):
        if decision.identity.startswith("database:") and self.database is not None:
            return self.database
        if decision.identity.startswith("backup:") and self.backup is not None:
            return self.backup
        if decision.identity.startswith("filestore:") and self.filestore is not None:
            return self.filestore
        if (
            decision.identity.startswith("volume:") or decision.identity.startswith("compose:")
        ) and self.compose is not None:
            return self.compose
        return None


__all__ = [
    "BackupPurgeAdapter",
    "ComposeClusterPurgeAdapter",
    "ComposeClusterPurgeTarget",
    "DatabasePurgeAdapter",
    "DatabasePurgeTarget",
    "FilestorePurgeAdapter",
    "FilestorePurgeTarget",
    "ProjectDataPurgeAdapters",
]
