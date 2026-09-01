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
    step_id: str | None = None,
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
            PreparedStep,
            ProcessExecutionError,
            SubprocessExecutor,
            active_context,
        )

        context = active_context()
        if context is not None:
            if step_id is None:
                from odoo_instance_sdk.exceptions import UnplannedStepError

                raise UnplannedStepError("psql process requires captured step_id")
            captured_step = context.prepared(step_id)
            if captured_step.argv != tuple(cmd) or captured_step.timeout != timeout:
                from odoo_instance_sdk.exceptions import UnplannedStepError

                raise UnplannedStepError(step_id)
            captured = cast("ProcessResult", context.process_prepared(captured_step))
            stdout = captured.stdout if isinstance(captured.stdout, str) else ""
            stderr = captured.stderr if isinstance(captured.stderr, str) else ""
            return subprocess.CompletedProcess(cmd, captured.returncode, stdout, stderr)
        captured = SubprocessExecutor().execute(
            PreparedStep(
                step_id=step_id or "psql",
                argv=tuple(cmd),
                environment=tuple(sorted(env.items())),
                environment_snapshot=tuple(sorted(env.items())),
                environment_overrides=((("PGPASSWORD", password),) if password is not None else ()),
                environment_policy="sanitized-inherit",
                timeout=timeout,
                read_only=True,
                text=True,
                secret_values=(password,) if password else (),
            )
        )
        stdout = captured.stdout if isinstance(captured.stdout, str) else ""
        stderr = captured.stderr if isinstance(captured.stderr, str) else ""
        return subprocess.CompletedProcess(cmd, captured.returncode, stdout, stderr)
    except (ProcessExecutionError, OSError):
        return None
