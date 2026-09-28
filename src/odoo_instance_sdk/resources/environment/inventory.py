"""Checkout inventory projection mixin."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.models import CheckoutInventory
    from odoo_instance_sdk.models.backup import DevelopmentEnvironment


class _CheckoutInventoryMixin:
    if TYPE_CHECKING:

        def list_command(
            self,
            *,
            project: ProjectConfig | Path | None = None,
            include_removed: bool = False,
        ) -> Command[list[DevelopmentEnvironment]]: ...

    def list(
        self,
        *,
        project: ProjectConfig | Path | None = None,
        include_removed: bool = False,
    ) -> list[DevelopmentEnvironment]:
        return self.list_command(
            project=project,
            include_removed=include_removed,
        ).run()

    def checkout_inventory_command(
        self,
        *,
        project_id: str | None = None,
        include_removed: bool = False,
    ) -> Command[CheckoutInventory]:
        """Delegate checkout inventory projection to the environment monitor."""
        from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

        return EnvironmentMonitor().checkout_inventory_command(
            project_id=project_id,
            include_removed=include_removed,
        )

    def checkout_inventory(
        self,
        *,
        project_id: str | None = None,
        include_removed: bool = False,
    ) -> CheckoutInventory:
        """Project one checkout inventory from a single monitor snapshot."""
        return self.checkout_inventory_command(
            project_id=project_id,
            include_removed=include_removed,
        ).run()
