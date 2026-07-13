"""Back up a production Odoo database and restore it onto a local staging instance.

Usage: uv run python examples/backup_and_restore.py
"""

import logging

from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.exceptions import DatabaseError, RemoteInstanceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refresh")

prod = OdooClient(
    OdooClientConfig(
        executable="odoo",
        base_url="http://odoo-prod.internal:8069",
        master_pwd="prod_master_pwd",
        backup_dir="/var/backups/odoo",
    )
)

staging = OdooClient(
    OdooClientConfig(
        executable="odoo",
        base_url="http://localhost:8069",
        master_pwd="staging_master_pwd",
    )
)

source_db = "production"
target_db = "production_staging"


def main() -> None:
    if not prod.database.exists(source_db):
        log.error("Source database '%s' not found on production", source_db)
        return

    log.info("Backing up '%s' from production", source_db)
    artifact = prod.database.backup(source_db, format="zip", include_filestore=True)
    log.info("Backup saved: %s", artifact.path)

    if staging.database.exists(target_db):
        log.info("Dropping stale staging database '%s'", target_db)
        staging.database.drop(target_db)

    try:
        staging.database.restore(artifact, target_db)
    except RemoteInstanceError:
        log.exception("Restore blocked by local-only guard")
        return
    except DatabaseError as e:
        log.exception("Restore failed (HTTP %s)", e.status_code)
        return

    log.info("Restored '%s' on staging", target_db)


if __name__ == "__main__":
    main()
