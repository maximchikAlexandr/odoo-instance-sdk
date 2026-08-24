from __future__ import annotations

from odoo_instance_sdk.internal.postgres_transport import run_psql


def database_size_bytes(
    *,
    host: str | None,
    port: int,
    user: str | None,
    password: str | None,
    database_name: str,
    timeout: float = 10.0,
) -> int | None:
    """Return ``pg_database_size(database_name)`` in bytes, or ``None`` on any failure.

    Mirrors ``_verify_database_via_psql`` in ``resources/database.py``: PGPASSWORD on
    env (never argv), single-quote escaping, ``-t -A`` raw output. ``None`` covers
    missing user, backslash injection guard, missing psql, non-zero exit, timeout,
    OSError, or unparseable stdout.
    """
    if "\\" in database_name:
        return None
    escaped = database_name.replace("'", "''")
    proc = run_psql(
        host=host,
        port=port,
        user=user,
        password=password,
        query=f"SELECT pg_database_size('{escaped}')",
        timeout=timeout,
    )
    if proc is None or proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    try:
        return int(out)
    except ValueError:
        return None
