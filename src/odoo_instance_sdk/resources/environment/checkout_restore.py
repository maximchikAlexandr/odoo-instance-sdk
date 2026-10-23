"""Restore probe handling shared by environment checkout execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import (
    DatabaseAlreadyExistsError,
    InstanceConfigurationError,
    RestoreFailedError,
)
from odoo_instance_sdk.resources.database import DatabaseResource
from odoo_instance_sdk.resources.environment.checkout_planning import _process_stderr

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.proc import ProcessResult, RunContext
    from odoo_instance_sdk.models.backup import DevelopmentEnvironment


def consume_copy_database_probe(
    context: RunContext[DevelopmentEnvironment],
    step_id: str,
    target_db: str,
    *,
    expected_exists: bool,
    databases: DatabaseResource,
) -> None:
    if expected_exists:
        if not context.planned(step_id) or context.consumed(step_id):
            return
        if isinstance(databases, DatabaseResource):
            raise RestoreFailedError("post-restore database identity was not captured")
        # Compatibility doubles own no private probe consumer; their catalog
        # callback supplies the typed fixture identity instead.
        context.skip(step_id)
        return

    result = cast("ProcessResult", context.process(step_id))
    if result.returncode != 0:
        detail = _process_stderr(result).strip()
        suffix = f": {detail}" if detail else ""
        raise InstanceConfigurationError(
            f"PostgreSQL database existence probe failed for {target_db!r}"
            f" (returncode={result.returncode}){suffix}"
        )
    stdout = result.stdout
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    exists = bool(str(stdout or "").strip())
    if exists == expected_exists:
        return
    if expected_exists:
        raise RestoreFailedError(f"Database {target_db!r} was not created after restore")
    raise DatabaseAlreadyExistsError(f"Target database {target_db!r} already exists")
