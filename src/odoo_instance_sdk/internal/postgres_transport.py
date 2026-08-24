"""Small CLI-free PostgreSQL transport primitives used by core collectors."""

from __future__ import annotations

import os
import shutil
import subprocess


def run_psql(
    *,
    host: str | None,
    port: int,
    user: str | None,
    password: str | None,
    query: str,
    timeout: float,
) -> subprocess.CompletedProcess[str] | None:
    """Run one read-only psql query without inheriting ambient credentials."""
    if user is None or shutil.which("psql") is None:
        return None
    env = os.environ.copy()
    env.pop("PGPASSWORD", None)
    if password is not None:
        env["PGPASSWORD"] = password
    # A missing endpoint means the local TCP endpoint, not libpq's Unix socket.
    endpoint = host if host is not None else "127.0.0.1"
    cmd = [
        "psql",
        "-h",
        endpoint,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        "postgres",
        "-t",
        "-A",
        "-c",
        query,
    ]
    try:
        return subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=timeout, shell=False, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
