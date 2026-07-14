"""Prepare a local development Odoo instance from a test environment backup.

Flow: backup from test → start local Odoo → restore backup → stop instance.

Usage: uv run python examples/prepare_dev_instance.py
"""

import logging
import sys

from odoo_instance_sdk import OdooClient, OdooClientConfig, StartConfig
from odoo_instance_sdk.exceptions import (
    DatabaseError,
    ProcessExitedBeforeReady,
    ReadinessTimeoutError,
    RemoteInstanceError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prepare-dev")

# Test environment — backup and list allowed on remote
test = OdooClient(
    OdooClientConfig(
        executable="odoo",
        base_url="http://odoo-test.internal:8069",
        master_pwd="test_master_pwd",
        backup_dir="/tmp/odoo-backups",
    )
)

# Local instance — restore and drop require localhost
local = OdooClient(
    OdooClientConfig(
        executable="odoo",
        base_url="http://localhost:8069",
        master_pwd="local_master_pwd",
    )
)

SOURCE_DB = "test_db"
LOCAL_DB = "dev_db"
PORT = 8069


def main() -> None:
    # 1. Back up from test environment
    if not test.database.exists(SOURCE_DB):
        log.error("Source database '%s' not found on test instance", SOURCE_DB)
        sys.exit(1)

    log.info("Backing up '%s' from test environment", SOURCE_DB)
    artifact = test.database.backup(SOURCE_DB, format="zip", include_filestore=True)
    log.info("Backup saved: %s", artifact.path)

    # 2. Start a local Odoo instance (needed for restore via HTTP)
    log.info("Starting local Odoo on port %s", PORT)
    proc = local.server.start(
        StartConfig(
            http_port=PORT,
            http_interface="127.0.0.1",
            db_host="localhost",
            db_user="odoo",
            db_password="odoo",
            log_level="info",
        )
    )

    try:
        # 3. Wait for the instance to become ready
        try:
            result = local.server.wait_ready(proc, timeout=120.0)
            log.info("Odoo ready in %.1fs (%d polls)", result.elapsed, result.attempts)
        except ReadinessTimeoutError:
            log.exception("Odoo did not become ready")
            sys.exit(1)
        except ProcessExitedBeforeReady:
            log.exception("Odoo process exited before becoming ready")
            sys.exit(1)

        # 4. Drop stale dev database if it exists
        if local.database.exists(LOCAL_DB):
            log.info("Dropping existing dev database '%s'", LOCAL_DB)
            local.database.drop(LOCAL_DB)

        # 5. Restore the backup onto the local instance
        try:
            local.database.restore(artifact, LOCAL_DB)
        except RemoteInstanceError:
            log.exception("Restore blocked by local-only guard")
            sys.exit(1)
        except DatabaseError as e:
            log.exception("Restore failed (HTTP %s)", e.status_code)
            sys.exit(1)

        log.info("Restored '%s' on local instance", LOCAL_DB)

    finally:
        # 6. Stop the local Odoo instance
        log.info("Stopping local Odoo (PID %s)", proc.pid)
        local.server.stop(proc, timeout=10.0)
        log.info("Stopped")

    log.info("Dev instance prepared. Start Odoo manually to use database '%s'", LOCAL_DB)


if __name__ == "__main__":
    main()
