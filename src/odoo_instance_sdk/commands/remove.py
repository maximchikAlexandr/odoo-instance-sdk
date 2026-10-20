"""Root project removal command and its same-object ``rm`` spelling."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    emit_json_envelope,
    fail,
    model_to_dict,
    output_options,
    render_rich_text,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.internal.project_removal_coordinator import (
    expected_project_confirmation_for_command,
    set_project_confirmation,
)
from odoo_instance_sdk.models.project_removal import ProjectRemovalResult, ProjectSelector


def _rich_remove(document: OutputDocument) -> str:
    if not document.ok:
        return f"Project removal failed: {document.error.message if document.error else 'operation failed'}"
    result = document.result if isinstance(document.result, dict) else {}
    return render_rich_text(
        f"Project {result.get('project_id', '<unknown>')} · "
        f"{result.get('mode', 'remove')} · "
        f"{result.get('registration_state', 'unknown')}"
    )


def _removal_result(value: ProjectRemovalResult | None) -> JsonObject:
    if value is None:
        return {}
    return model_to_dict(value)


def _selector(project_path: str | None, project_id: str | None) -> ProjectSelector:
    if (project_path is None) == (project_id is None):
        raise click.UsageError("exactly one of --project or --project-id is required")
    return (
        ProjectSelector.from_path(project_path)
        if project_path is not None
        else ProjectSelector.from_project_id(project_id or "")
    )


@click.command("remove", aliases=["rm"], help="Forget or purge one registered project.")
@click.option("--project", "project_path", type=click.Path(exists=False), default=None)
@click.option("--project-id", type=str, default=None)
@click.option("--purge", is_flag=True, default=False, help="Delete exclusively owned resources.")
@click.option("--confirm-project", type=str, default=None, help="Exact purge confirmation token.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the captured plan only.")
@click.option(
    "--yes", is_flag=True, default=False, help="Compatibility flag; never confirms purge."
)
@output_options
def remove(
    project_path: str | None,
    project_id: str | None,
    purge: bool,
    confirm_project: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Forget a project, or capture a guarded purge plan."""
    del yes
    output_mode = resolve_output_mode(output_format, json_output)
    selector = _selector(project_path, project_id)
    if purge and not dry_run and output_mode is not OutputMode.RICH and confirm_project is None:
        emit_json_envelope(
            ok=False,
            command="remove",
            error_code="confirmation_required",
            error_message="remove --purge requires --confirm-project",
            mode=output_mode,
        )
        raise click.exceptions.Exit(1)

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    try:
        command = client.projects.remove_command(
            selector,
            purge=purge,
            confirmation=confirm_project,
        )
        if purge and not dry_run and output_mode is OutputMode.RICH and confirm_project is None:
            token = expected_project_confirmation_for_command(command)
            set_project_confirmation(
                command,
                click.prompt(f"Type the exact project confirmation token ({token})", type=str),
            )
        run_or_preview(
            lambda: command,
            command_name="remove",
            mode=output_mode,
            dry_run=dry_run,
            result=_removal_result,
            rich=_rich_remove,
        )
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        fail(output_mode, "remove", exc, dry_run=dry_run)


__all__ = ["remove"]
