from __future__ import annotations

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.resources.environment import DevelopmentEnvironment


def backup_exists(client: OdooClient, env: DevelopmentEnvironment) -> bool | None:
    """Return whether the environment backup is available, or None if unset."""
    if env.backup_id is None:
        return None
    return any(backup.id == env.backup_id for backup in client.backups.list())
