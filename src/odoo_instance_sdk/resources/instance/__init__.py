from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.instance.auxiliary_restore import (
    AuxiliaryRestoreSession,
    _attach_auxiliary_restore_runtime,
    activate_auxiliary_restore_session,
    active_auxiliary_restore_session,
    auxiliary_restore_session,
    reset_auxiliary_restore_session,
    resolve_runtime_argv,
)
from odoo_instance_sdk.resources.instance.identity import _IdentityMixin
from odoo_instance_sdk.resources.instance.planning import _PlanningMixin
from odoo_instance_sdk.resources.instance.runtime import (
    InstanceFactory,
    _RuntimeBinding,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.git import GitResource
    from odoo_instance_sdk.resources.module import ModuleResource
    from odoo_instance_sdk.resources.postgres import PostgresCluster


@dataclass(slots=True, kw_only=True)
class OdooInstance(_IdentityMixin, _PlanningMixin):
    config: InstanceConfig
    _client: OdooClient
    databases: DatabaseResource = field(init=False)
    modules: ModuleResource = field(init=False)
    git: GitResource = field(init=False)
    _artifact_lock_path: Path | None = field(default=None, repr=False)
    _postgres_cluster: PostgresCluster | None = field(default=None, repr=False)
    _environment_id: str | None = field(default=None, repr=False)
    _runtime_binding: _RuntimeBinding | None = field(default=None, repr=False)


__all__ = [
    "AuxiliaryRestoreSession",
    "InstanceFactory",
    "OdooInstance",
    "_attach_auxiliary_restore_runtime",
    "activate_auxiliary_restore_session",
    "active_auxiliary_restore_session",
    "auxiliary_restore_session",
    "reset_auxiliary_restore_session",
    "resolve_runtime_argv",
]
