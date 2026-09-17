"""Lazy CLI dependencies shared by env command modules."""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor


def _env_module() -> ModuleType:
    import odoo_instance_sdk.commands.env as env_pkg

    return env_pkg


def _client_class() -> type[OdooClient]:
    return cast("type[OdooClient]", getattr(_env_module(), "OdooClient"))


def _client_config_class() -> type[OdooClientConfig]:
    return cast("type[OdooClientConfig]", getattr(_env_module(), "OdooClientConfig"))


def _monitor_class() -> type[EnvironmentMonitor]:
    return cast("type[EnvironmentMonitor]", getattr(_env_module(), "EnvironmentMonitor"))
