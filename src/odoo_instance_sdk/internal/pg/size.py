"""PostgreSQL size collection built on the shared native transport."""

from __future__ import annotations

from odoo_instance_sdk.internal.pg.transport import run_psql


def database_size_bytes(
    *,
    host: str | None,
    port: int,
    user: str | None,
    password: str | None,
    database_name: str,
    timeout: float = 10.0,
) -> int | None:
    """Return ``pg_database_size(database_name)`` in bytes, or ``None`` on failure."""
    if "\\" in database_name:
        return None
    escaped = database_name.replace("'", "''")
    proc = run_psql(
        host=host if host is not None else "127.0.0.1",
        port=port,
        user=user,
        password=password,
        query=f"SELECT pg_database_size('{escaped}')",
        timeout=timeout,
    )
    if proc is None or proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None
