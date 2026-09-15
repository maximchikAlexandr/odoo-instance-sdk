"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

_discovered = {
    path.stem for path in Path(__file__).parent.glob("*.py") if path.name != "__init__.py"
}
_ORDER = ["checkout", "list", "show", "remove", "sync"]
_SUBMODULES = [name for name in _ORDER if name in _discovered] + sorted(_discovered - set(_ORDER))

for _module_name in _SUBMODULES:
    _module = importlib.import_module(f"odoo_instance_sdk.commands.env.{_module_name}")
    for _key, _value in _module.__dict__.items():
        if _key.startswith("__"):
            continue
        globals()[_key] = _value


def __getattr__(
    name: str,
) -> type[OdooClient | OdooClientConfig | EnvironmentMonitor | EnvironmentCheckoutOptions]:
    """Preserve lazy exports for tests and callers."""
    import odoo_instance_sdk.commands.env.deps as env_deps

    return cast(
        "type[OdooClient | OdooClientConfig | EnvironmentMonitor | EnvironmentCheckoutOptions]",
        getattr(env_deps, name),
    )
