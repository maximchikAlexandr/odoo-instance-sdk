"""Public command registration for the environment command group."""

from __future__ import annotations

from odoo_instance_sdk.commands.env.checkout import env_group as env_group
from odoo_instance_sdk.commands.env.checkout import env_list as env_list

__all__ = [
    "env_group",
    "env_list",
]
