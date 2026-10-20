"""Public project lifecycle facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from odoo_instance_sdk.models.project_removal import (
    ProjectRemovalResult,
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
        from odoo_instance_sdk.internal.project_removal_coordinator import (
            capture_project_removal_command,
        )

        return capture_project_removal_command(
            self._client.get_catalog(),
            selector,
            purge=purge,
            confirmation=confirmation,
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
