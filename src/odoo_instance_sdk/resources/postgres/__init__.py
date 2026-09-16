"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

from odoo_instance_sdk.internal import paths as _paths  # noqa: F401
from odoo_instance_sdk.resources.postgres.backup_restore import *  # noqa: F403
from odoo_instance_sdk.resources.postgres.backup_restore_parts import (
    PostgresCluster as PostgresCluster,
)
from odoo_instance_sdk.resources.postgres.diagnostics import *  # noqa: F403
from odoo_instance_sdk.resources.postgres.lifecycle import (
    _resolve_project_id as _resolve_project_id,
)
