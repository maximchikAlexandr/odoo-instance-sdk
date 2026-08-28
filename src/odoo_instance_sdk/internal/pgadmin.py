"""Public pgAdmin lifecycle orchestration."""

from __future__ import annotations

import time

from odoo_instance_sdk.exceptions import PgAdminUnavailableError
from odoo_instance_sdk.internal import pgadmin_container, pgadmin_files
from odoo_instance_sdk.models import PgAdminOpenResult


def open_pgadmin_lifecycle(
    *,
    environment: object,
    instance: object,
    cluster: object,
    database: str,
    timeout: float = 60.0,
) -> PgAdminOpenResult:
    """Resolve, prepare, and reconcile the one user-global pgAdmin container."""
    del environment
    runner = getattr(cluster, "compose_runner", None)
    if runner is None:
        raise PgAdminUnavailableError()
    try:
        deadline = time.monotonic() + max(0.1, timeout)
        paths = pgadmin_files.PgAdminPaths.from_defaults()
        with pgadmin_files.pgadmin_lock(path=paths.lock, timeout=timeout):
            identity = pgadmin_container.resolve_postgres_identity(cluster, deadline=deadline)
            existing = pgadmin_container.inspect_container(
                runner, pgadmin_files.PGADMIN_CONTAINER_NAME, deadline=deadline, missing_ok=True
            )
            if existing is not None:
                pgadmin_container.assert_owned_container(existing)
            password = getattr(getattr(instance, "config", None), "db_password", None) or ""
            preparation = pgadmin_files.prepare_files(
                servers_json=pgadmin_files.server_json(identity, database),
                pgpass=pgadmin_files.pgpass_line(identity, password),
                fingerprint=pgadmin_files.server_fingerprint(paths, identity, database, password),
                port=pgadmin_files.select_port(paths),
                paths=paths,
            )
            return pgadmin_container.reconcile_container(
                preparation,
                runner=runner,
                network=identity.network,
                database=database,
                deadline=deadline,
            )
    except PgAdminUnavailableError:
        raise
    except Exception:
        raise PgAdminUnavailableError() from None


__all__ = ["open_pgadmin_lifecycle"]
