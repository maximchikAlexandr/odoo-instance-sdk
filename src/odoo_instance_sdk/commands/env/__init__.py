"""Public command registration for the environment command group."""

from __future__ import annotations  # noqa: I001 -- keep public environment command re-exports grouped; remove when Ruff supports grouped aliases.

from odoo_instance_sdk.commands.env.checkout import env_group as env_group, env_list as env_list

__all__ = [
    "env_group",
    "env_list",
]
