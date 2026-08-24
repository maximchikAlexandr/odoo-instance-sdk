from __future__ import annotations

import os
import shutil
import subprocess


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
    if user is None:
        return None
    if "\\" in database_name:
        return None
    if shutil.which("psql") is None:
        return None
    env = os.environ.copy()
    if password is not None:
        env["PGPASSWORD"] = password
    escaped = database_name.replace("'", "''")
    cmd: list[str] = ["psql"]
    if host is not None:
        cmd.extend(["-h", host])
    cmd.extend(
        [
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            "postgres",
            "-t",
            "-A",
            "-c",
            f"SELECT pg_database_size('{escaped}')",
        ]
    )
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    try:
        return int(out)
    except ValueError:
        return None
