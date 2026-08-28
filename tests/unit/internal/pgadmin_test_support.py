from __future__ import annotations

from pathlib import Path

from odoo_instance_sdk.internal import pgadmin_files


def _paths(root: Path) -> pgadmin_files.PgAdminPaths:
    private = root / "pgadmin" / "private"
    return pgadmin_files.PgAdminPaths(
        root=private.parent,
        private_dir=private,
        data_dir=private.parent / "data",
        admin_password=private / "admin-password",
        pgpass=private / ".pgpass",
        servers_json=private / "servers.json",
        metadata=private / "metadata.json",
        lock=root / "locks" / "pgadmin.lock",
    )
