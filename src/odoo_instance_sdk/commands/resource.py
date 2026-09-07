"""Bounded read-only local resource projections."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.execution import Command, PlanObservation
    from odoo_instance_sdk.internal.proc import (
        PreparedCommand,
        RunContext,
        Step,
        SubprocessExecutor,
    )
else:
    import rich_click as click

from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands.context import CliContext, resolve_project_path
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    emit,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    success_document,
)
from odoo_instance_sdk.internal.paths import get_backups_dir, get_catalog_path, get_data_root
from odoo_instance_sdk.internal.resource_inventory import (
    FileResourceSource,
    OwnershipConfidence,
    ProjectResourceSource,
    ResourceInventory,
    VolumeResourceSource,
    collect_resource_inventory,
    open_catalog_read_only,
)
from odoo_instance_sdk.models import ClusterResourceSnapshot

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.pg.inventory import DatabaseInventoryResult
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


class _CatalogPathProvider:
    provider: Callable[[], Path] | None = None


_catalog_path_provider = _CatalogPathProvider()


def configure_catalog_path_provider(provider: Callable[[], Path]) -> None:
    """Use the same imported CLI catalogue seam as backup commands."""
    _catalog_path_provider.provider = provider


def _catalog_path() -> Path:
    provider = _catalog_path_provider.provider
    if provider is not None:
        return provider()
    return get_catalog_path(ensure_exists=False)


def _inspection_backups_dir() -> Path:
    return get_backups_dir(ensure_exists=False)


def _project_root() -> Path | None:
    current = click.get_current_context(silent=True)
    if current is None:
        return None
    context = current.find_object(CliContext)
    if not isinstance(context, CliContext):
        return None
    try:
        return resolve_project_path(context).resolve()
    except Exception:
        return None


_RESOURCE_STEP = "resource.inventory"


@dataclass(slots=True)
class _ResourcePlan:
    catalog: BackupCatalog | None
    data_root: Path
    backup_root: Path
    project: ProjectResourceSource | None = None
    cluster: PostgresCluster | None = None
    claim_id: str | None = None
    database_command: PreparedCommand[DatabaseInventoryResult] | None = None
    snapshot_command: PreparedCommand[ClusterResourceSnapshot | None] | None = None
    observations: tuple[PlanObservation, ...] = ()
    files: tuple[FileResourceSource, ...] = ()
    database_reason: str | None = None


def _skip_remaining(
    context: RunContext[ResourceInventory], prepared: PreparedCommand[ResourceInventory]
) -> None:
    for step in prepared.steps:
        if context.planned(step.step_id) and not context.consumed(step.step_id):
            context.skip(step.step_id)


def _fail_child_actions(
    context: RunContext[ResourceInventory],
    steps: tuple[Step, ...],
    error: BaseException,
) -> None:
    for step in steps:
        context.fail_action(step.step_id, error)


def _project_file_sources(  # noqa: C901
    project: ProjectConfig,
    instance: OdooInstance | None,
    *,
    cluster: PostgresCluster | None,
    claim_id: str | None,
    catalog: BackupCatalog | None,
    project_id: str,
) -> tuple[tuple[FileResourceSource, ...], str | None]:
    """Capture configured/provenance files without claiming external ownership."""
    from odoo_instance_sdk.internal.odoo_config import parse_db_names
    from odoo_instance_sdk.models import StartConfig

    project_config = project
    instance_config = instance.config if instance is not None else None
    start_config = getattr(instance_config, "start_config", None)
    if start_config is None and project_config.source_config is not None:
        try:
            start_config = StartConfig.from_odoo_config(project_config.source_config)
        except Exception:
            start_config = None
    configured_databases = set(getattr(instance_config, "configured_database_names", ()))
    if start_config is not None:
        configured_databases.update(parse_db_names(start_config.db_name))
    if project_config.default_source_database:
        configured_databases.add(project_config.default_source_database)

    rows: list[tuple[Path, str, OwnershipConfidence, str | None]] = []
    reason: str | None = None
    cluster_endpoint = cluster.endpoint if cluster is not None else None
    database_cluster = cluster_endpoint
    if catalog is not None and cluster is not None:
        try:
            bindings = catalog._list_restore_bindings(
                cluster.endpoint_host,
                cluster.endpoint_port,
            )
        except Exception:
            bindings = []
            reason = "restore provenance probe was unavailable"
        for row in bindings:
            data_directory = row["data_directory"]
            database = row["database_name"]
            if not isinstance(data_directory, str) or not isinstance(database, str):
                continue
            path = Path(data_directory) / "filestore" / database
            proven = claim_id is not None and row["cluster_id"] == claim_id
            owner_identity = None
            if proven:
                assert claim_id is not None
                owner_identity = database_resource_identity(claim_id, database)
            rows.append(
                (
                    path,
                    database,
                    OwnershipConfidence.PROVEN if proven else OwnershipConfidence.UNKNOWN,
                    owner_identity,
                )
            )

    files: list[FileResourceSource] = []
    if start_config is not None and start_config.logfile:
        files.append(
            FileResourceSource(
                path=Path(start_config.logfile),
                kind="log",
                owner_identity=f"project:{project_id}",
                ownership=OwnershipConfidence.PROVEN,
            )
        )
    proven_paths = {
        path.resolve(strict=False): owner_identity
        for path, _database, ownership, owner_identity in rows
        if ownership is OwnershipConfidence.PROVEN and owner_identity is not None
    }
    if start_config is not None and start_config.data_dir:
        for database in sorted(configured_databases):
            path = Path(start_config.data_dir) / "filestore" / database
            proven_identity = proven_paths.get(path.resolve(strict=False))
            rows.append(
                (
                    path,
                    database,
                    OwnershipConfidence.PROVEN
                    if proven_identity is not None
                    else OwnershipConfidence.UNKNOWN,
                    proven_identity,
                )
            )
    for path, database, ownership, owner_identity_value in rows:
        resolved_owner_identity = owner_identity_value or (
            database_resource_identity(database_cluster, database)
            if database_cluster is not None
            else None
        )
        files.append(
            FileResourceSource(
                path=path,
                kind="filestore",
                owner_identity=resolved_owner_identity,
                ownership=ownership,
            )
        )
    return tuple(files), reason


def database_resource_identity(cluster: str, database: str) -> str:
    """Build the canonical cluster-qualified identity used by the graph builder."""
    from odoo_instance_sdk.internal.resource_inventory import _database_identity

    return _database_identity(cluster, database)


def _build_resource_plan(process_executor: SubprocessExecutor) -> _ResourcePlan:  # noqa: C901
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.internal.pg.inventory import build_database_inventory_command
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.postgres import PostgresCluster, _resolve_project_id

    catalog: BackupCatalog | None = None
    path = _catalog_path()
    if path.is_file():
        with contextlib.suppress(Exception):
            catalog = open_catalog_read_only(path)
    plan = _ResourcePlan(
        catalog=catalog,
        data_root=get_data_root(ensure_exists=False),
        backup_root=_inspection_backups_dir(),
    )
    project_root = _project_root()
    if project_root is None:
        return plan

    project = ProjectConfig.load(project_root)
    project_id = _resolve_project_id(project_root)
    project_source = ProjectResourceSource(
        project_id=project_id,
        repository_root=project_root,
        runtime_database=project.default_source_database,
    )
    # The project leaf is captured before any optional cluster/configuration
    # probe so a missing external config cannot erase initialized-project data.
    plan.project = project_source

    cluster: PostgresCluster | None = None
    with contextlib.suppress(Exception):
        cluster = PostgresCluster.from_project(project_root)
    plan.cluster = cluster
    claim = None
    if catalog is not None:
        with contextlib.suppress(Exception):
            claim = catalog._get_postgres_cluster(project_id)
    claim_id = (
        str(claim.cluster_id)
        if claim is not None
        and claim.state == "active"
        and cluster is not None
        and cluster.mode == "compose"
        else None
    )
    plan.claim_id = claim_id
    database_cluster = cluster.endpoint if cluster is not None else None
    plan.project = replace(project_source, database_cluster=database_cluster)

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    if catalog is not None:
        client._catalog = catalog
    instance: OdooInstance | None = None
    with contextlib.suppress(Exception):
        instance = client.instance.from_project(project)
    if instance is None and project.source_config is not None:
        with contextlib.suppress(Exception):
            instance = client.instance.from_config(project.source_config)
            if cluster is not None:
                instance._postgres_cluster = cluster

    files, file_reason = _project_file_sources(
        project,
        instance,
        cluster=cluster,
        claim_id=claim_id,
        catalog=catalog,
        project_id=project_id,
    )
    plan.files = files
    plan.database_reason = file_reason
    if instance is not None and cluster is not None:
        try:
            database_command = build_database_inventory_command(
                instance,
                project_root,
                catalog=catalog,
                executor=process_executor,
            )
            plan.observations += database_command.plan.observations
            plan.database_command = database_command._prepared()
        except Exception:
            plan.database_reason = (
                plan.database_reason or "PostgreSQL inventory probe was unavailable"
            )
    elif cluster is None:
        plan.database_reason = (
            plan.database_reason or "initialized project PostgreSQL configuration unavailable"
        )
    else:
        plan.database_reason = (
            plan.database_reason or "initialized project PostgreSQL credentials unavailable"
        )

    if cluster is not None and cluster.mode == "compose":
        try:
            snapshot_command = cluster.resource_snapshot_command(executor=process_executor)
            plan.observations += snapshot_command.plan.observations
            plan.snapshot_command = snapshot_command._prepared()
        except Exception:
            plan.database_reason = plan.database_reason or "volume probe unavailable"
    return plan


def _volume_source(
    plan: _ResourcePlan,
    snapshot: ClusterResourceSnapshot | None,
) -> tuple[VolumeResourceSource, ...]:
    cluster = plan.cluster
    if cluster is None or cluster.mode != "compose":
        return ()
    from odoo_instance_sdk.internal.postgres_compose import compose_volume_name

    metrics = getattr(snapshot, "metrics", None)
    unavailable = getattr(snapshot, "unavailability_reason", None)
    return (
        VolumeResourceSource(
            project_id=cluster._project_id,
            volume_name=compose_volume_name(cluster._project_id),
            cluster_id=plan.claim_id,
            owned=plan.claim_id is not None,
            available=metrics is not None,
            usage_bytes=getattr(metrics, "volume_usage_bytes", None),
            active=(getattr(snapshot, "container", None) is not None)
            if snapshot is not None
            else None,
            reason=(str(unavailable) if unavailable is not None else "volume probe unavailable"),
        ),
    )


def _resource_command() -> Command[ResourceInventory]:
    from odoo_instance_sdk.execution import Command, ExecutionPlan
    from odoo_instance_sdk.internal.proc import PreparedAction, SubprocessExecutor, prepared_command

    process_executor = SubprocessExecutor()
    resource_action = PreparedAction(
        step_id=_RESOURCE_STEP,
        action="collect-resource-inventory",
        description="Collect read-only local resource inventory",
        read_only=True,
    )
    plan = _build_resource_plan(process_executor)
    prepared_children = tuple(
        child for child in (plan.database_command, plan.snapshot_command) if child is not None
    )
    steps = (resource_action, *(step for child in prepared_children for step in child.steps))

    def run(context: RunContext[ResourceInventory]) -> ResourceInventory:
        context.action(_RESOURCE_STEP)
        database_inventory: DatabaseInventoryResult | None = None
        snapshot: ClusterResourceSnapshot | None = None
        database_reason = plan.database_reason
        try:
            if plan.database_command is not None:
                try:
                    database_callback = cast(
                        "Callable[[RunContext[ResourceInventory]], DatabaseInventoryResult]",
                        plan.database_command.callback,
                    )
                    database_inventory = database_callback(context)
                except Exception as error:
                    _fail_child_actions(context, plan.database_command.steps, error)
                    _skip_remaining(
                        context,
                        cast("PreparedCommand[ResourceInventory]", plan.database_command),
                    )
                    database_reason = (
                        database_reason or "PostgreSQL inventory probe was unavailable"
                    )
            if plan.snapshot_command is not None:
                try:
                    snapshot_callback = cast(
                        "Callable[[RunContext[ResourceInventory]], ClusterResourceSnapshot | None]",
                        plan.snapshot_command.callback,
                    )
                    snapshot = snapshot_callback(context)
                except Exception as error:
                    _fail_child_actions(context, plan.snapshot_command.steps, error)
                    _skip_remaining(
                        context,
                        cast("PreparedCommand[ResourceInventory]", plan.snapshot_command),
                    )
                    database_reason = database_reason or "volume probe was unavailable"
            inventory = collect_resource_inventory(
                catalog=plan.catalog,
                database_inventory=database_inventory,
                projects=(plan.project,) if plan.project is not None else (),
                volumes=_volume_source(plan, snapshot),
                files=plan.files,
                roots=(plan.data_root,),
                backup_root=plan.backup_root,
                owned_directories=(plan.backup_root,),
                database_measurement_reason=database_reason,
            )
            context.complete_action(_RESOURCE_STEP)
            return inventory
        finally:
            if plan.catalog is not None:
                plan.catalog.close()

    return Command.from_prepared(
        ExecutionPlan(
            steps=tuple(step.public_projection() for step in steps),
            observations=plan.observations,
        ),
        prepared_command(run, steps, executor=process_executor),
    )


def _payload(inventory: ResourceInventory) -> JsonObject:
    return model_to_dict(inventory)


def _rich_list(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    resources = result.get("resources", [])
    if not isinstance(resources, list):
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    table = Table(
        "Identity", "Type", "Name", "Ownership", "Measured bytes", "Complete", "Reclaimable"
    )
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        table.add_row(
            str(resource.get("stable_identity", "")),
            str(resource.get("type", "")),
            str(resource.get("name", "")),
            str(resource.get("ownership_confidence", "")),
            str(resource.get("measured_bytes", "")),
            str(resource.get("completeness", "")),
            str(resource.get("reclaimable", False)).lower(),
        )
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _rich_doctor(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    findings = result.get("findings", [])
    if not isinstance(findings, list):
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    if not findings:
        return "No resource findings."
    table = Table("Severity", "Code", "Identity", "Message", "Recommendation")
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        table.add_row(
            str(finding.get("severity", "")),
            str(finding.get("code", "")),
            str(finding.get("stable_identity", "")),
            str(finding.get("message", "")),
            str(finding.get("recommendation", "") or ""),
        )
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _run_resource(
    command_name: str,
    mode: OutputMode,
    rich: Callable[[OutputDocument], str],
) -> None:
    try:
        inventory = _resource_command().run()
        emit(success_document(command=command_name, result=_payload(inventory)), mode, rich=rich)
    except Exception as exc:
        fail(mode, command_name, exc)


@click.group("resource", help="Inspect retained local resources and findings.")
def resource_group() -> None:
    """Inspect retained local resources and findings."""


@resource_group.command("list", help="List read-only local resource observations.")
@output_options
def resource_list(output_format: str | None, json_output: bool) -> None:
    _run_resource("resource.list", resolve_output_mode(output_format, json_output), _rich_list)


@resource_group.command("doctor", help="Diagnose read-only local resource findings.")
@output_options
def resource_doctor(output_format: str | None, json_output: bool) -> None:
    _run_resource("resource.doctor", resolve_output_mode(output_format, json_output), _rich_doctor)


__all__ = ["configure_catalog_path_provider", "resource_group"]
