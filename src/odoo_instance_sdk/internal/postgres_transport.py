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
    """Run one read-only psql query with explicit connection inputs.

    Password-file authentication remains available when ``password`` is None.
    """
    if user is None or shutil.which("psql") is None:
        return None
    env = os.environ.copy()
    env.pop("PGPASSWORD", None)
    # The caller's explicit transport choice must not be silently overridden
    # by libpq environment settings.  In particular, PGHOST/PGHOSTADDR would
    # turn the host=None socket path into TCP, or redirect an explicit host.
    # PGPASSFILE deliberately remains: it is the supported non-interactive
    # authentication mechanism when no password was configured.
    for key in (
        "PSQLRC",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGOPTIONS",
        "PGHOST",
        "PGHOSTADDR",
    ):
        env.pop(key, None)
    if password is not None:
        env["PGPASSWORD"] = password
    cmd = [
        "psql",
        "-X",
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
    # ``None`` deliberately leaves host selection to libpq (Unix socket).
    # Monitor callers that require loopback TCP pass 127.0.0.1 explicitly.
    if host is not None:
        cmd[2:2] = ["-h", host]
    try:
        return subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=timeout, shell=False, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
