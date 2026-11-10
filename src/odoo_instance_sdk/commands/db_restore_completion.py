"""Rich projection for the existing database-restore completion mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich_click import UsageError

from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputMode,
    _InspectableCommand,
    bordered_table,
    render_rich_text,
)
from odoo_instance_sdk.exceptions import AdminPasswordRequiredError
from odoo_instance_sdk.internal.cli_format import rich_cell

if TYPE_CHECKING:
    from pathlib import Path

    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.models import RestoreCompletionResult


def validate_completion_options(
    complete: str | None,
    backup_uuid: str | None,
    archive_file: Path | None,
    target: str | None,
    replace: bool,
    reset_password: bool,
    modules: tuple[str, ...],
) -> None:
    if complete is not None and any(
        (
            backup_uuid is not None,
            archive_file is not None,
            target is not None,
            replace,
            reset_password,
        )
    ):
        raise UsageError(
            "--complete cannot be combined with restore source, --target, --replace, or --reset-admin-password"
        )
    if complete is None and modules:
        raise UsageError("--update-module requires --complete")


def completion_command(
    client: OdooClient,
    project: Path,
    target: str,
    *,
    modules: tuple[str, ...],
    mode: OutputMode,
    no_input: bool,
    dry_run: bool,
) -> _InspectableCommand[RestoreCompletionResult]:
    """Resolve a reset secret only when the captured checkpoint needs one."""
    try:
        return client.environments.complete_restore_command(project, target, update_modules=modules)
    except AdminPasswordRequiredError:
        from odoo_instance_sdk.internal.admin_password import resolve_admin_password_secret

        password, provenance = resolve_admin_password_secret(
            prompt=mode is OutputMode.RICH and not no_input and not dry_run,
            project_root=project,
        )
        return client.environments.complete_restore_command(
            project,
            target,
            update_modules=modules,
            admin_password=password,
            admin_password_provenance=provenance,
        )


def restore_completion_rich(payload: JsonObject) -> str:
    """Render the typed completion summary without exposing process details."""
    table = bordered_table("Field", "Value", title="Post-restore completion")
    for field in (
        "target_database",
        "checkpoint",
        "completed_stages",
        "pending_stage",
        "requested_modules",
        "previous_default",
        "effective_default",
        "published",
        "no_op",
    ):
        value = payload.get(field)
        if value is not None:
            table.add_row(field.replace("_", " ").title(), rich_cell(value))
    return render_rich_text(table, width=Console().width)
