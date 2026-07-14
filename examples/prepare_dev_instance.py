"""Prepare a local development Odoo instance from a test environment backup.

Flow: read odoo.conf → check last backup → download if stale (>2h) or reuse
→ start local Odoo → restore → stop.

Configure via environment variables (see .env.example). Export them or use a .env loader:
    set -a && source .env && set +a && uv run python examples/prepare_dev_instance.py
"""

import logging
import os
from datetime import UTC, datetime, timedelta

from odoo_instance_sdk import Backup, OdooClient, OdooClientConfig, StartConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prepare-dev")

config = OdooClientConfig(executable=os.environ.get("ODOO_EXECUTABLE", "odoo"))
client = OdooClient(config)

# Test environment — backup allowed on remote
test_instance = client.instance(
    base_url=os.environ["TEST_ODOO_BASE_URL"],
    master_password=os.environ["TEST_ODOO_MASTER_PASSWORD"],
)

# Local instance — all params from odoo.conf
local_instance = client.instance.from_config(os.environ["ODOO_CONF_PATH"])
FRESH_THRESHOLD = timedelta(hours=2)


def get_or_download_backup(source_db: str) -> Backup:
    """Return the latest backup if it's fresh (<2h), otherwise download a new one."""
    latest = client.backups.latest(
        source_base_url=test_instance.config.base_url,
        database_name=source_db,
    )
    if latest is not None:
        age = datetime.now(UTC) - latest.downloaded_at
        if age < FRESH_THRESHOLD:
            log.info(
                "Reusing fresh backup %s (age: %.0f min)", latest.filename, age.total_seconds() / 60
            )
            return latest

    log.info("Backing up '%s' from test environment", source_db)
    backup = test_instance.databases.backup(source_db)
    log.info("Backup saved: %s", backup.filename)
    return backup


def main() -> None:
    dbs = test_instance.databases.list()
    if not dbs:
        raise RuntimeError("No databases found on test instance")

    source_db = dbs[0]
    log.info("Resolved source database: %s (available: %s)", source_db, ", ".join(dbs))

    local_db = f"{source_db}_{datetime.now(UTC):%Y%m%d_%H%M%S}"
    backup = get_or_download_backup(source_db)

    log.info("Starting local Odoo from %s", os.environ["ODOO_CONF_PATH"])
    proc = local_instance.start(StartConfig.from_odoo_config(os.environ["ODOO_CONF_PATH"]))

    try:
        local_instance.wait_ready(proc, timeout=120.0)

        if local_instance.databases.exists(local_db):
            log.info("Dropping existing dev database '%s'", local_db)
            local_instance.databases.drop(local_db)

        local_instance.databases.restore(backup, local_db, copy=False)
        log.info("Restored '%s' on local instance", local_db)

    finally:
        log.info("Stopping local Odoo (PID %s)", proc.pid)
        local_instance.stop(proc, timeout=10.0)

    log.info("Dev instance prepared. Start Odoo manually to use database '%s'", local_db)


if __name__ == "__main__":
    main()
