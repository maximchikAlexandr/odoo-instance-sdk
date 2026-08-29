"""Small CLI-free PostgreSQL transport primitives used by core collectors."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.internal.process_env import sanitized_child_environment

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import ProcessResult


def run_psql(
    *,
    host: str | None,
    port: int,
    user: str | None,
    password: str | None,
    query: str,
    timeout: float,
    database: str = "postgres",
) -> subprocess.CompletedProcess[str] | None:
    """Run one read-only psql query with explicit connection inputs.

    Password-file authentication remains available when ``password`` is None.
    """
    if user is None or shutil.which("psql") is None:
        return None
    env = sanitized_child_environment()
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
        database,
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
        from odoo_instance_sdk.internal.proc import (
            ProcessExecutionError,
            SubprocessExecutor,
            active_context,
            prepared_step,
        )

        context = active_context()
        if context is not None:
            captured = cast(
                "ProcessResult",
                context.process_prepared(
                    prepared_step(
                        cmd, env=env, environment_policy="explicit", timeout=timeout, text=True
                    )
                ),
            )
            stdout = captured.stdout if isinstance(captured.stdout, str) else ""
            stderr = captured.stderr if isinstance(captured.stderr, str) else ""
            return subprocess.CompletedProcess(cmd, captured.returncode, stdout, stderr)
        captured = SubprocessExecutor().execute(
            prepared_step(cmd, env=env, environment_policy="explicit", timeout=timeout, text=True)
        )
        stdout = captured.stdout if isinstance(captured.stdout, str) else ""
        stderr = captured.stderr if isinstance(captured.stderr, str) else ""
        return subprocess.CompletedProcess(cmd, captured.returncode, stdout, stderr)
    except (ProcessExecutionError, OSError):
        return None
