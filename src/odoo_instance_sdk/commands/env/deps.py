"""Lazy CLI dependencies shared by env command modules."""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions
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


def __getattr__(
    name: str,
) -> type[OdooClient | OdooClientConfig | EnvironmentMonitor | EnvironmentCheckoutOptions]:
    """Resolve operation dependencies only when a command or test requests them."""
    if name == "OdooClient":
        from odoo_instance_sdk.client import OdooClient

        globals()[name] = OdooClient
        return OdooClient
    if name == "OdooClientConfig":
        from odoo_instance_sdk.config import OdooClientConfig

        globals()[name] = OdooClientConfig
        return OdooClientConfig
    if name == "EnvironmentCheckoutOptions":
        from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

        globals()[name] = EnvironmentCheckoutOptions
        return EnvironmentCheckoutOptions
    if name == "EnvironmentMonitor":
        from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

        globals()[name] = EnvironmentMonitor
        return EnvironmentMonitor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
