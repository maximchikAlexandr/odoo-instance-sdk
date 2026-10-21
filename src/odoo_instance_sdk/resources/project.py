"""Public project lifecycle facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from odoo_instance_sdk.models.project_removal import (
    ProjectRemovalResult,
    ProjectResourceDecision,
    ProjectSelector,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command


@dataclass(slots=True, kw_only=True)
class ProjectResource:
    """Typed entry point for project registration and removal operations."""

    _client: OdooClient

    def remove_command(  # noqa: C901
        self,
        selector: ProjectSelector,
        *,
        purge: bool = False,
        confirmation: str | None = None,
    ) -> Command[ProjectRemovalResult]:
        """Capture one immutable project removal command."""
        import msgspec

        from odoo_instance_sdk.config import InstanceConfig
        from odoo_instance_sdk.exceptions import (
            ConfigError,
            InstanceConfigurationError,
            PostgresClusterError,
        )
        from odoo_instance_sdk.internal.pg.inventory import build_database_inventory_command
        from odoo_instance_sdk.internal.postgres_compose import (
            container_id_matches_snapshot,
            inspect_container_identity,
            inspect_volume_identity,
        )
        from odoo_instance_sdk.internal.project_data_purge import (
            BackupPurgeAdapter,
            ComposeClusterPurgeAdapter,
            ComposeClusterPurgeTarget,
            DatabasePurgeAdapter,
            DatabasePurgeTarget,
            FilestorePurgeAdapter,
            FilestorePurgeTarget,
            ProjectDataPurgeAdapters,
        )
        from odoo_instance_sdk.internal.project_filesystem_purge import (
            FilesystemPurgeAdapter,
            FilesystemPurgeTarget,
            project_owned_paths,
        )
        from odoo_instance_sdk.internal.project_removal import (
            ProjectRegistration,
            resolve_project_selector,
        )
        from odoo_instance_sdk.internal.project_removal_coordinator import (
            ProjectRemovalAdapter,
            capture_project_removal_command,
        )
        from odoo_instance_sdk.internal.project_removal_runtime import (
            RuntimeEnvironmentPurgeAdapters,
        )
        from odoo_instance_sdk.internal.resource_inventory import (
            VolumeResourceSource,
            stable_resource_identity,
        )
        from odoo_instance_sdk.models import DatabaseInventoryResult, StartConfig
        from odoo_instance_sdk.project import (
            PostgresProjectConfig,
            ProjectConfig,
            managed_filestore_path,
        )
        from odoo_instance_sdk.resources.instance import OdooInstance
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        catalog = self._client.get_catalog()
        registrations = tuple(
            ProjectRegistration(
                project_id=str(row["project_id"]),
                repository_root=Path(str(row["repository_root"])),
                git_common_dir=Path(str(row["git_common_dir"])),
                registration_state=cast(
                    "Literal['registered', 'forgotten']", str(row["registration_state"])
                ),
            )
            for row in catalog.list_projects(include_forgotten=True)
        )
        registration = resolve_project_selector(selector, registrations)

        client = self._client
        project_config: ProjectConfig | None
        try:
            project_config = ProjectConfig.load(registration.repository_root)
        except (ConfigError, OSError, ValueError):
            project_config = None
        managed_filestore = (
            managed_filestore_path(project_config) if project_config is not None else None
        )
        database_inventory = None
        volume_observation: VolumeResourceSource | None = None
        claim = catalog._get_postgres_cluster_for_project(
            registration.project_id,
            registration.repository_root,
            registration.git_common_dir,
        )
        instance = None
        recovered_source = False
        recovery_endpoint: tuple[str, int] | None = None
        if (
            purge
            and project_config is None
            and claim is not None
            and claim.state == "active"
            and not registration.repository_root.exists()
            and not registration.repository_root.is_symlink()
            and registration.git_common_dir is not None
        ):
            endpoint_row = catalog._conn.execute(
                """SELECT db_host, db_port FROM database_events
                   WHERE cluster_id = ? AND event_type = 'bootstrapped'
                   ORDER BY sequence DESC LIMIT 1""",
                (str(claim.cluster_id),),
            ).fetchone()
            endpoint = (
                (str(endpoint_row["db_host"]), int(endpoint_row["db_port"]))
                if endpoint_row is not None
                else None
            )
            recovery_endpoint = endpoint
            if endpoint is not None:
                recovery_config = ProjectConfig(
                    repository_root=registration.repository_root,
                    postgres=PostgresProjectConfig(
                        mode="compose", image="pgvector/pgvector:pg16", port=endpoint[1]
                    ),
                )
                try:
                    recovery_cluster = PostgresCluster._from_config(
                        recovery_config,
                        repository_root=registration.repository_root,
                        compose_runner=None,
                        project_id=claim.project_id,
                    )
                    instance = OdooInstance(
                        config=InstanceConfig(
                            base_url="http://127.0.0.1:8069",
                            start_config=StartConfig(
                                db_host=endpoint[0],
                                db_port=endpoint[1],
                            ),
                            db_host=endpoint[0],
                            db_port=endpoint[1],
                        ),
                        _client=client,
                        _postgres_cluster=recovery_cluster,
                    )
                    project_config = recovery_config
                    managed_filestore = managed_filestore_path(project_config)
                    recovered_source = True
                except (OSError, ValueError, PostgresClusterError):
                    instance = None
        if purge and project_config is not None:
            if instance is None:
                try:
                    instance = client.instance.from_project(project_config)
                except (
                    ConfigError,
                    InstanceConfigurationError,
                    OSError,
                    ValueError,
                    PostgresClusterError,
                ):
                    instance = None
            if instance is not None:
                try:
                    if recovered_source:
                        database_inventory = build_database_inventory_command(
                            instance,
                            registration.repository_root,
                            tracked=False,
                            catalog=catalog,
                            project=project_config,
                        ).run()
                        durable_names = set(
                            catalog._project_restored_database_names(
                                registration.project_id,
                                claim.cluster_id if claim is not None else "",
                                *(recovery_endpoint or ("", 0)),
                            )
                            if claim is not None and recovery_endpoint is not None
                            else ()
                        )
                        if recovery_endpoint is not None and claim is not None:
                            durable_names.update(
                                catalog._project_bound_database_names(
                                    claim.project_id,
                                    claim.cluster_id,
                                    *recovery_endpoint,
                                )
                            )
                            durable_names.update(
                                catalog._project_bootstrapped_database_names(
                                    claim.cluster_id, *recovery_endpoint
                                )
                            )
                        observed_names = {
                            item.name
                            for item in database_inventory.databases
                            if item.name not in {"postgres", "template0", "template1"}
                        }
                        authorized_names = observed_names & durable_names
                        if not authorized_names or observed_names - durable_names:
                            database_inventory = None
                        else:
                            database_inventory = DatabaseInventoryResult(
                                cluster=database_inventory.cluster,
                                databases=tuple(
                                    item
                                    for item in database_inventory.databases
                                    if item.name in authorized_names
                                    and item.name not in {"postgres", "template0", "template1"}
                                ),
                                tracked=False,
                            )
                        if database_inventory is not None:
                            database_inventory = DatabaseInventoryResult(
                                cluster=database_inventory.cluster,
                                databases=tuple(
                                    msgspec.structs.replace(
                                        item,
                                        cluster_id=(
                                            str(claim.cluster_id)
                                            if item.cluster_id is None and claim is not None
                                            else item.cluster_id
                                        ),
                                    )
                                    for item in database_inventory.databases
                                ),
                                tracked=False,
                            )
                        if database_inventory is not None and database_inventory.databases:
                            project_config = ProjectConfig(
                                repository_root=registration.repository_root,
                                default_source_database=database_inventory.databases[0].name,
                                postgres=project_config.postgres,
                            )
                    else:
                        database_inventory = instance.databases.list_inventory(
                            registration.repository_root,
                            tracked=True,
                        )
                except (ConfigError, OSError, ValueError, PostgresClusterError):
                    database_inventory = None
                if claim is not None and claim.state == "active":
                    cluster = cast(
                        "PostgresCluster | None", getattr(instance, "_postgres_cluster", None)
                    )
                    if cluster is not None and getattr(cluster, "owned", False):
                        try:
                            volume_verified = inspect_volume_identity(
                                cluster.compose_runner,
                                claim.volume_name,
                                str(claim.cluster_id),
                                project_id=claim.project_id,
                            )
                            inspected_container_id = inspect_container_identity(
                                cluster.compose_runner,
                                f"{claim.compose_project}-postgres-1",
                                str(claim.cluster_id),
                                project_id=claim.project_id,
                                compose_project=claim.compose_project,
                                volume_name=claim.volume_name,
                            )
                            snapshot = cluster.resource_snapshot()
                        except (OSError, ValueError, PostgresClusterError):
                            volume_verified = False
                            inspected_container_id = None
                            snapshot = None
                        container = getattr(snapshot, "container", None)
                        stopped = getattr(snapshot, "unavailability_reason", None) == "stopped"
                        metrics = getattr(snapshot, "metrics", None)
                        volume_observation = VolumeResourceSource(
                            project_id=registration.project_id,
                            volume_name=claim.volume_name,
                            cluster_id=str(claim.cluster_id),
                            owned=True,
                            available=volume_verified and (stopped or container is not None),
                            usage_bytes=(
                                getattr(metrics, "volume_usage_bytes", None)
                                if volume_verified
                                else None
                            ),
                            active=(
                                False
                                if volume_verified and stopped
                                else True
                                if volume_verified and container is not None
                                else None
                            ),
                            container_identity_verified=(
                                volume_verified
                                and inspected_container_id is not None
                                and container is not None
                                and container_id_matches_snapshot(
                                    inspected_container_id,
                                    getattr(container, "id", None),
                                )
                            ),
                            reason=(
                                None
                                if volume_verified and (stopped or container is not None)
                                else "Compose volume or container observation unavailable"
                            ),
                        )

        class _Adapters:
            def __init__(self) -> None:
                self._runtime = RuntimeEnvironmentPurgeAdapters(client)
                self._filesystem = FilesystemPurgeAdapter(
                    (
                        *(
                            FilesystemPurgeTarget(
                                identity=stable_resource_identity(
                                    kind, str(path.resolve(strict=False))
                                ),
                                project_id=registration.project_id,
                                path=path,
                                allowed_root=registration.repository_root,
                            )
                            for kind, path in project_owned_paths(
                                registration.repository_root,
                                managed_filestore=managed_filestore,
                            )
                            if kind != "filestore"
                        ),
                        FilesystemPurgeTarget(
                            identity=f"project:{registration.project_id}",
                            project_id=registration.project_id,
                            path=registration.repository_root,
                            allowed_root=registration.repository_root.parent,
                            git_common_dir=registration.git_common_dir,
                        ),
                    )
                )
                data = ProjectDataPurgeAdapters(
                    backup=BackupPurgeAdapter(client, catalog),
                )
                if project_config is not None:
                    database_names = (
                        tuple(item.name for item in database_inventory.databases)
                        if recovered_source and database_inventory is not None
                        else (
                            (project_config.default_source_database,)
                            if project_config.default_source_database
                            else ()
                        )
                    )
                    database_targets = {
                        f"database:{database}": DatabasePurgeTarget(
                            identity=f"database:{database}",
                            project_id=registration.project_id,
                            project_root=registration.repository_root,
                            database_name=database,
                            instance=instance,
                            project_config=project_config,
                        )
                        for database in database_names
                    }
                    if database_targets:
                        database_identities = dict(database_targets)
                        for database, database_target in (
                            (name, target) for name, target in database_targets.items()
                        ):
                            database_name = database.removeprefix("database:")
                            database_identities[
                                stable_resource_identity(
                                    "database", f"127.0.0.1:5432\x00{database_name}"
                                )
                            ] = database_target
                            if claim is not None and claim.state == "active":
                                database_identities[
                                    stable_resource_identity(
                                        "database", f"{claim.cluster_id}\x00{database_name}"
                                    )
                                ] = database_target
                        data.database = DatabasePurgeAdapter(
                            client,
                            database_identities,
                        )
                    filestore = managed_filestore_path(project_config)
                    filestore_target = FilestorePurgeTarget(
                        identity=stable_resource_identity("filestore", str(filestore)),
                        project_id=registration.project_id,
                        path=filestore,
                        allowed_root=filestore.parent,
                        database_provenance_check=lambda: (
                            ProjectConfig.load(registration.repository_root).default_source_database
                            == project_config.default_source_database
                        ),
                    )
                    data.filestore = FilestorePurgeAdapter(
                        {filestore_target.identity: filestore_target}
                    )
                if claim is not None and claim.state == "active":
                    scheduled_environment_states = frozenset({"ready", "failed", "cleanup_failed"})
                    capture_dependency_check_pending = True

                    def _compose_dependencies() -> tuple[str, ...]:
                        return tuple(
                            f"environment:{row['id']}"
                            for row in catalog.list_environments(
                                project_id=registration.project_id,
                                include_removed=False,
                            )
                            if str(row["state"]) not in scheduled_environment_states
                        )

                    def _compose_dependency_check() -> bool:
                        nonlocal capture_dependency_check_pending
                        if capture_dependency_check_pending:
                            capture_dependency_check_pending = False
                            return bool(_compose_dependencies())
                        return bool(
                            catalog.list_environments(
                                project_id=registration.project_id,
                                include_removed=False,
                            )
                        )

                    compose_target = ComposeClusterPurgeTarget(
                        identity=stable_resource_identity(
                            "volume", f"{registration.project_id}\x00{claim.volume_name}"
                        ),
                        project_id=registration.project_id,
                        cluster_id=str(claim.cluster_id),
                        compose_project=claim.compose_project,
                        container_name=f"{claim.compose_project}-postgres-1",
                        volume_name=claim.volume_name,
                        claim_project_id=claim.project_id,
                        dependencies=_compose_dependencies(),
                        dependency_check=_compose_dependency_check,
                    )
                    data.compose = ComposeClusterPurgeAdapter(
                        catalog,
                        {
                            compose_target.identity: compose_target,
                            f"volume:{claim.volume_name}": compose_target,
                            f"compose:{claim.cluster_id}": compose_target,
                        },
                    )
                self._data = data

            def for_decision(
                self, decision: ProjectResourceDecision
            ) -> ProjectRemovalAdapter | None:
                runtime = self._runtime.for_decision(decision)
                return (
                    runtime
                    or self._filesystem.for_decision(decision)
                    or self._data.for_decision(decision)
                )

        adapters = _Adapters()

        return capture_project_removal_command(
            catalog,
            selector,
            purge=purge,
            confirmation=confirmation,
            adapters=adapters,
            database_inventory=database_inventory,
            volume_observation=volume_observation,
        )

    def remove(
        self,
        selector: ProjectSelector,
        *,
        purge: bool = False,
        confirmation: str | None = None,
    ) -> ProjectRemovalResult:
        """Execute the command captured by :meth:`remove_command`."""

        return self.remove_command(
            selector,
            purge=purge,
            confirmation=confirmation,
        ).run()


__all__ = ["ProjectResource"]
