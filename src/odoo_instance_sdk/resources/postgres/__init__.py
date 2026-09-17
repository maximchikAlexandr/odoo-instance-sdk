"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import time as time

from odoo_instance_sdk.internal import paths as _paths  # noqa: F401
from odoo_instance_sdk.internal.address import probe_address as probe_address
from odoo_instance_sdk.internal.postgres_compose import docker_available as docker_available
from odoo_instance_sdk.resources.postgres.backup_restore_parts import *  # noqa: F403
from odoo_instance_sdk.resources.postgres.backup_restore_parts import (
    PostgresCluster as PostgresCluster,
)
from odoo_instance_sdk.resources.postgres.lifecycle import (
    _resolve_project_id as _resolve_project_id,
)
