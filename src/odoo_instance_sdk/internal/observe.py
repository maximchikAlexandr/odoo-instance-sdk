from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


def backup_exists(client: OdooClient, env: Any) -> bool | None:
    if env.backup_id is None:
        return None
    row = client.get_catalog().get_by_id(str(env.backup_id))
    return row is not None and bool(row["path"]) and Path(str(row["path"])).is_file()
