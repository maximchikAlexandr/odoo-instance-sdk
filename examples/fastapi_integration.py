"""FastAPI service: one endpoint that refreshes staging from production.

POST /refresh — backs up the production database, restores it onto a local
staging Odoo instance, then starts the instance so it is ready to serve.

Logging is minimal: only the key state transitions (backup done, restore done,
server started).

Usage: uv run uvicorn examples.fastapi_integration:app --reload
"""

import contextlib
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from odoo_instance_sdk import OdooClient, OdooClientConfig, StartConfig
from odoo_instance_sdk.exceptions import (
    DatabaseError,
    ProcessNotFoundError,
    RemoteInstanceError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refresh-api")

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

app = FastAPI(title="Odoo Staging Refresh")


class RefreshRequest(BaseModel):
    source_db: str = "production"
    target_db: str = "production_staging"
    http_port: int = 8069


class RefreshResponse(BaseModel):
    source_db: str
    target_db: str
    backup_path: str
    process_id: str
    pid: int


@app.post("/refresh", response_model=RefreshResponse)
def refresh(req: RefreshRequest) -> RefreshResponse:
    """Back up production DB → restore on staging → start staging Odoo."""

    # 1. Backup from production
    try:
        artifact = prod.database.backup(req.source_db, format="zip", include_filestore=True)
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail=f"Backup failed: {e.message}") from e

    log.info("Backup of '%s' saved to %s", req.source_db, artifact.path)

    # 2. Drop stale staging DB if present
    if staging.database.exists(req.target_db):
        try:
            staging.database.drop(req.target_db)
        except (DatabaseError, RemoteInstanceError) as e:
            raise HTTPException(status_code=500, detail=f"Drop failed: {e}") from e

    # 3. Restore backup onto staging
    try:
        staging.database.restore(artifact, req.target_db)
    except RemoteInstanceError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except DatabaseError as e:
        raise HTTPException(status_code=502, detail=f"Restore failed: {e.message}") from e

    log.info("Restored '%s' on staging", req.target_db)

    # 4. Start the staging Odoo server
    proc = staging.server.start(StartConfig(http_port=req.http_port))
    log.info("Staging Odoo started (PID %s)", proc.pid)

    return RefreshResponse(
        source_db=req.source_db,
        target_db=req.target_db,
        backup_path=str(artifact.path),
        process_id=proc.id,
        pid=proc.pid,
    )


@app.post("/shutdown")
def shutdown() -> dict[str, str]:
    """Stop all running staging Odoo processes."""
    stopped: list[str] = []
    for proc in list(staging._processes.values()):
        with contextlib.suppress(ProcessNotFoundError):
            staging.server.stop(proc, timeout=10.0)
            stopped.append(proc.id)
    if stopped:
        log.info("Stopped %d process(es)", len(stopped))
    return {"stopped": ",".join(stopped) if stopped else "none"}
