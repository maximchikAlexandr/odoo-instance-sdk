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
                            for kind, path in project_owned_paths(registration.repository_root)
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

            def for_decision(
                self, decision: ProjectResourceDecision
            ) -> ProjectRemovalAdapter | None:
                runtime = self._runtime.for_decision(decision)
                return runtime or self._filesystem.for_decision(decision)

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
