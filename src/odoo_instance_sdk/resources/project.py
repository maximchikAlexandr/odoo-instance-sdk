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

    def remove_command(
        self,
        selector: ProjectSelector,
        *,
        purge: bool = False,
        confirmation: str | None = None,
    ) -> Command[ProjectRemovalResult]:
        """Capture one immutable project removal command."""
        from odoo_instance_sdk.exceptions import ConfigError
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
        from odoo_instance_sdk.internal.resource_inventory import stable_resource_identity
        from odoo_instance_sdk.project import ProjectConfig, managed_filestore_path

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
                claim = catalog._get_postgres_cluster(registration.project_id)
                data = ProjectDataPurgeAdapters(
                    backup=BackupPurgeAdapter(client, catalog),
                )
                if project_config is not None:
                    database = project_config.default_source_database
                    if database:
                        database_target = DatabasePurgeTarget(
                            identity=f"database:{database}",
                            project_id=registration.project_id,
                            project_root=registration.repository_root,
                            database_name=database,
                        )
                        data.database = DatabasePurgeAdapter(
                            client,
                            {
                                database_target.identity: database_target,
                                stable_resource_identity(
                                    "database", f"127.0.0.1:5432\x00{database}"
                                ): database_target,
                                **(
                                    {
                                        stable_resource_identity(
                                            "database", f"{claim.cluster_id}\x00{database}"
                                        ): database_target
                                    }
                                    if claim is not None and claim.state == "active"
                                    else {}
                                ),
                            },
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
