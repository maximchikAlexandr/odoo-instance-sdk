"""Public command registration for the environment command group."""

from __future__ import annotations  # noqa: I001

from odoo_instance_sdk.commands.env.checkout import env_group as env_group, env_list as env_list

__all__ = [
    "env_group",
    "env_list",
]
