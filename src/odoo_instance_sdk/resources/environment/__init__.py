from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from odoo_instance_sdk.resources.environment.checkout import _CheckoutMixin
from odoo_instance_sdk.resources.environment.cleanup import _CleanupMixin
from odoo_instance_sdk.resources.environment.pgadmin import _PgadminMixin
from odoo_instance_sdk.resources.environment.settings import _SettingsMixin

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


@dataclass(slots=True, kw_only=True)
class EnvironmentResource(_CheckoutMixin, _SettingsMixin, _CleanupMixin, _PgadminMixin):
    _client: OdooClient


from odoo_instance_sdk.resources.environment.checkout_planning import (
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
    _checkout_public_plan,
)

__all__ = [
    "DevelopmentEnvironment",
    "EnvironmentCheckoutOptions",
    "EnvironmentDatabaseMode",
    "EnvironmentResource",
    "EnvironmentState",
    "_checkout_public_plan",
]
