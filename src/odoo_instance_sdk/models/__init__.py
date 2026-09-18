"""Public typed models re-exported from domain submodules."""

from __future__ import annotations  # noqa: I001

from odoo_instance_sdk.models._literals import (
    ClusterUnavailabilityReason as ClusterUnavailabilityReason,
    ModuleJsonValue as ModuleJsonValue,
    ServerUnavailabilityReason as ServerUnavailabilityReason,
)
from odoo_instance_sdk.models.backup import *  # noqa: F403
from odoo_instance_sdk.models.command import *  # noqa: F403
from odoo_instance_sdk.models.config import *  # noqa: F403
from odoo_instance_sdk.models.database_inventory import *  # noqa: F403
from odoo_instance_sdk.models.deps import *  # noqa: F403
from odoo_instance_sdk.models.footprint import *  # noqa: F403
from odoo_instance_sdk.models.git import *  # noqa: F403
from odoo_instance_sdk.models.module import *  # noqa: F403
from odoo_instance_sdk.models.monitor import *  # noqa: F403
from odoo_instance_sdk.models.postgres import *  # noqa: F403
from odoo_instance_sdk.models.process_inventory import *  # noqa: F403
from odoo_instance_sdk.models.runtime import *  # noqa: F403
from odoo_instance_sdk.models.testing import *  # noqa: F403
