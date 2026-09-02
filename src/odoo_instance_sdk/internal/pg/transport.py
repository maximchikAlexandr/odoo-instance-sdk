"""PostgreSQL transport through the shared process executor."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, cast

from .builder import PsqlSpecification, build_psql_specification

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import ProcessResult


def execute_psql(specification: PsqlSpecification) -> ProcessResult:
    """Consume the exact captured step, or execute it at the shared boundary."""
    from odoo_instance_sdk.internal.proc import SubprocessExecutor, active_context

    step = specification.prepared_step
    context = active_context()
    if context is not None:
        captured = context.prepared(step.step_id)
        if captured != step:
            from odoo_instance_sdk.exceptions import UnplannedStepError

            raise UnplannedStepError(step.step_id, reason="psql specification changed")
        return cast("ProcessResult", context.process_prepared(step))
    return SubprocessExecutor().execute(step)


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
    """Run one read-only psql query using the canonical private specification."""
    if user is None:
        return None
    try:
        from odoo_instance_sdk.internal.proc import active_context

        specification = build_psql_specification(
            host=host,
            port=port,
            user=user,
            database=database,
            password=password,
            args=("-c", query),
            _trusted_args=("-t", "-A"),
            timeout=timeout,
            step_id=step_id or "psql",
            _inject_timeout=active_context() is not None,
        )
        result = execute_psql(specification)
    except (OSError, RuntimeError):
        return None
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    return subprocess.CompletedProcess(
        specification.prepared_step.argv, result.returncode, stdout, stderr
    )


__all__ = ["PsqlSpecification", "build_psql_specification", "execute_psql", "run_psql"]
