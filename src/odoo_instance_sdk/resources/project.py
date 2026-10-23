"""Public project lifecycle facade.

The coordinator is intentionally supplied by the project-removal implementation
package. This facade fixes the SDK contract without doing discovery during
client construction.
"""

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
        """Capture a project removal command.

        The cross-resource coordinator is intentionally implemented by a later
        delivery package; keeping this contract inert prevents facade creation
        from performing discovery or mutation.
        """

        del selector, purge, confirmation
        raise NotImplementedError("project removal coordinator is not installed")

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
