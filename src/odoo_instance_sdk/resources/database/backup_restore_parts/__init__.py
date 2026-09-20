from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.resources.database.backup_restore_parts.backup import _BackupMixin
from odoo_instance_sdk.resources.database.backup_restore_parts.queries import _QueriesMixin

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.internal.proc import ProcessExecutor
    from odoo_instance_sdk.models import DatabaseInventoryResult
    from odoo_instance_sdk.resources.instance import OdooInstance


@dataclass(slots=True, kw_only=True)
class DatabaseResource(_QueriesMixin, _BackupMixin):
    base_url: str
    master_password: str | None = field(repr=False, default=None)
    _instance: OdooInstance = field(repr=False, hash=False, compare=False)

    def list_inventory(
        self,
        project_root: str | Path,
        *,
        tracked: bool = False,
    ) -> DatabaseInventoryResult:
        return self.list_inventory_command(project_root, tracked=tracked).run()

    def list_inventory_command(
        self,
        project_root: str | Path,
        *,
        tracked: bool = False,
        executor: ProcessExecutor | None = None,
    ) -> Command[DatabaseInventoryResult]:
        from odoo_instance_sdk.internal.pg.inventory import build_database_inventory_command

        return build_database_inventory_command(
            self._instance,
            project_root,
            tracked=tracked,
            executor=executor,
        )
