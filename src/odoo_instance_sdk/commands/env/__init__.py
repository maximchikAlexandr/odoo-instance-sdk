"""Split package; public imports preserved via re-exports."""

from __future__ import annotations

from rich.live import Live

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
    _render_env_list_rich as _render_env_list_rich,
)
from odoo_instance_sdk.commands.env.checkout import (
    _resolve_monitor_project_id as _resolve_monitor_project_id,
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
    _ticket_rich_lines as _ticket_rich_lines,
)
from odoo_instance_sdk.commands.env.checkout import (
    _TicketAllocation as _TicketAllocation,
)
from odoo_instance_sdk.commands.env.checkout import env_group as env_group
from odoo_instance_sdk.commands.env.checkout import (
    select_snapshot_environment as select_snapshot_environment,
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

_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    "EnvironmentMonitor": (
        "odoo_instance_sdk.resources.monitor",
        "EnvironmentMonitor",
    ),
    "OdooClient": ("odoo_instance_sdk.client", "OdooClient"),
    "OdooClientConfig": ("odoo_instance_sdk.config", "OdooClientConfig"),
    "EnvironmentCheckoutOptions": (
        "odoo_instance_sdk.resources.environment",
        "EnvironmentCheckoutOptions",
    ),
    "click": ("rich_click", None),
    "_ENV_LIST_COLUMNS": ("odoo_instance_sdk.commands.env.display", "_ENV_LIST_COLUMNS"),
    "_ENV_LIST_COMPACT_COLUMNS": (
        "odoo_instance_sdk.commands.env.display",
        "_ENV_LIST_COMPACT_COLUMNS",
    ),
    "_ENV_LIST_MEDIUM_COLUMNS": (
        "odoo_instance_sdk.commands.env.display",
        "_ENV_LIST_MEDIUM_COLUMNS",
    ),
    "env_list": ("odoo_instance_sdk.commands.env.checkout", "env_list"),
    "get_catalog_path": ("odoo_instance_sdk.internal.paths", "get_catalog_path"),
    "time": ("time", None),
}


def __getattr__(name: str) -> object:
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = spec
    from importlib import import_module

    module = import_module(module_name)
    value = module if attr_name is None else getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = [
    "EnvironmentCheckoutOptions",
    "EnvironmentMonitor",
    "Live",
    "OdooClient",
    "OdooClientConfig",
    "_TicketAllocation",
    "_build_ticket_checkout_command",
    "_catalog_worktree_paths",
    "_catalogue_branch_names",
    "_monitor_class",
    "_render_env_list_rich",
    "_resolve_monitor_project_id",
    "_resolve_ticket_allocation",
    "_revalidate_ticket_absence",
    "_ticket_checkout_command",
    "_ticket_provenance",
    "_ticket_rich_lines",
    "env_group",
    "env_list",
    "local_branch_names",
    "remote_branch_names",
    "resolve_environment",
    "resolve_project_path",
    "rev_parse_git_common_dir",
    "rev_parse_toplevel",
    "select_snapshot_environment",
]
