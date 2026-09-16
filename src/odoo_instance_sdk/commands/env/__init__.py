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

from odoo_instance_sdk.commands.context import resolve_environment, resolve_project_path
from odoo_instance_sdk.commands.env.checkout import (
    _build_ticket_checkout_command as _build_ticket_checkout_command,
)
from odoo_instance_sdk.commands.env.checkout import (
    _catalog_worktree_paths as _catalog_worktree_paths,
)
from odoo_instance_sdk.commands.env.checkout import (
    _catalogue_branch_names as _catalogue_branch_names,
)
from odoo_instance_sdk.commands.env.checkout import (
    _resolve_ticket_allocation as _resolve_ticket_allocation,
)
from odoo_instance_sdk.commands.env.checkout import (
    _revalidate_ticket_absence as _revalidate_ticket_absence,
)
from odoo_instance_sdk.commands.env.checkout import (
    _ticket_checkout_command as _ticket_checkout_command,
)
from odoo_instance_sdk.commands.env.checkout import (
    _ticket_provenance as _ticket_provenance,
)
from odoo_instance_sdk.commands.env.checkout import (
    _TicketAllocation as _TicketAllocation,
)
from odoo_instance_sdk.commands.env.deps import _monitor_class as _monitor_class
from odoo_instance_sdk.internal.git_worktree import (
    local_branch_names as local_branch_names,
)
from odoo_instance_sdk.internal.git_worktree import (
    remote_branch_names as remote_branch_names,
)
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_git_common_dir as rev_parse_git_common_dir,
)
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_toplevel as rev_parse_toplevel,
)

__all__ = [
    "_TicketAllocation",
    "_build_ticket_checkout_command",
    "_catalog_worktree_paths",
    "_catalogue_branch_names",
    "_monitor_class",
    "_resolve_ticket_allocation",
    "_revalidate_ticket_absence",
    "_ticket_checkout_command",
    "_ticket_provenance",
    "env_group",
    "local_branch_names",
    "remote_branch_names",
    "resolve_environment",
    "resolve_project_path",
    "rev_parse_git_common_dir",
    "rev_parse_toplevel",
]


def __getattr__(
    name: str,
) -> type[OdooClient | OdooClientConfig | EnvironmentMonitor | EnvironmentCheckoutOptions]:
    """Preserve lazy exports for tests and callers."""
    import odoo_instance_sdk.commands.env.deps as env_deps

    return cast(
        "type[OdooClient | OdooClientConfig | EnvironmentMonitor | EnvironmentCheckoutOptions]",
        getattr(env_deps, name),
    )
