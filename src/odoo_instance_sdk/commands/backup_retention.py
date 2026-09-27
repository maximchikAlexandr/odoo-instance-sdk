"""Retention and pinning projections for the backup command group."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.commands import backup as _backup_commands
from odoo_instance_sdk.commands.backup import backup_group
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    OutputMode,
    emit,
    fail,
    failure_document,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    success_document,
)
from odoo_instance_sdk.models import (
    BackupPinResult,
    BackupPruneResult,
    BackupRetentionUpdateResult,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _rich_retention(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    policy = result.get("policy", result)
    if not isinstance(policy, dict):
        return ""
    return (
        f"Retention: {policy.get('retention_days')} days; "
        f"automatic prune={'on' if policy.get('auto_prune') else 'off'}; "
        f"path={policy.get('path')}"
    )


def _rich_prune(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    plan = result.get("plan")
    if isinstance(plan, dict):
        candidates = plan.get("candidates", [])
        protected = plan.get("protected", [])
        skipped = plan.get("skipped", [])
        return (
            f"Prune plan: {len(candidates) if isinstance(candidates, list) else 0} candidate(s), "
            f"{len(protected) if isinstance(protected, list) else 0} protected, "
            f"{len(skipped) if isinstance(skipped, list) else 0} skipped."
        )
    deleted_ids = result.get("deleted_ids", [])
    return f"Pruned {len(deleted_ids) if isinstance(deleted_ids, list) else 0} backup(s)."


@backup_group.command("retention", help="Inspect or update user backup retention settings.")
@click.option("--days", "retention_days", type=click.IntRange(min=1), default=None)
@click.option("--auto/--no-auto", "auto_prune", default=None, help="Enable or disable auto-prune.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the update plan only.")
@output_options
def backup_retention(
    retention_days: int | None,
    auto_prune: bool | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    catalog: BackupCatalog | None = None
    try:
        catalog = _backup_commands._catalog()
        _client, resource = _backup_commands._backup_resource(catalog)
        if retention_days is None and auto_prune is None and not dry_run:
            policy = resource.retention()
            emit(
                success_document(command="backup.retention", result=model_to_dict(policy)),
                mode,
                rich=_rich_retention,
            )
            return
        status, value = run_or_preview(
            lambda: resource.set_retention_command(
                retention_days=retention_days,
                auto_prune=auto_prune,
                dry_run=dry_run,
            ),
            command_name="backup.retention",
            mode=mode,
            dry_run=dry_run,
            result=lambda item: model_to_dict(cast("BackupRetentionUpdateResult", item)),
            emit_normal=False,
            rich=_rich_retention,
        )
        if not dry_run:
            assert value is not None
            emit(
                success_document(command="backup.retention", result=model_to_dict(value)),
                mode,
                rich=_rich_retention,
            )
        if status:
            return
    except Exception as exc:
        fail(mode, "backup.retention", exc, dry_run=dry_run)
    finally:
        if catalog is not None:
            catalog.close()


def _run_backup_pin(
    backup_id: str,
    *,
    pinned: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    catalog: BackupCatalog | None = None
    try:
        catalog = _backup_commands._catalog()
        _client, resource = _backup_commands._backup_resource(catalog)
        status, value = run_or_preview(
            lambda: resource.set_pinned_command(backup_id, pinned, dry_run=dry_run),
            command_name="backup.pin" if pinned else "backup.unpin",
            mode=mode,
            dry_run=dry_run,
            result=lambda item: model_to_dict(cast("BackupPinResult", item)),
            emit_normal=False,
        )
        if not dry_run:
            assert value is not None
            emit(
                success_document(
                    command="backup.pin" if pinned else "backup.unpin", result=model_to_dict(value)
                ),
                mode,
            )
        if status:
            return
    except Exception as exc:
        fail(mode, "backup.pin" if pinned else "backup.unpin", exc, dry_run=dry_run)
    finally:
        if catalog is not None:
            catalog.close()


@backup_group.command("pin", help="Protect one retained backup from pruning.")
@click.argument("backup_id")
@click.option("--dry-run", is_flag=True, default=False, help="Show the pin plan only.")
@output_options
def backup_pin(backup_id: str, dry_run: bool, output_format: str | None, json_output: bool) -> None:
    _run_backup_pin(
        backup_id,
        pinned=True,
        dry_run=dry_run,
        output_format=output_format,
        json_output=json_output,
    )


@backup_group.command("unpin", help="Remove pruning protection from one backup.")
@click.argument("backup_id")
@click.option("--dry-run", is_flag=True, default=False, help="Show the unpin plan only.")
@output_options
def backup_unpin(
    backup_id: str, dry_run: bool, output_format: str | None, json_output: bool
) -> None:
    _run_backup_pin(
        backup_id,
        pinned=False,
        dry_run=dry_run,
        output_format=output_format,
        json_output=json_output,
    )


@backup_group.command("prune", help="Preview or remove eligible project backup archives.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview candidates without deletion.")
@click.option("--yes", is_flag=True, default=False, help="Confirm deletion without prompting.")
@output_options
@pass_cli_context
def backup_prune(
    ctx: CliContext,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    if not dry_run and not yes and mode is not OutputMode.RICH:
        emit(
            failure_document(
                command="backup.prune",
                dry_run=False,
                error_code="confirmation_required",
                error_message="backup prune requires --yes in machine output mode",
            ),
            mode,
        )
        raise click.exceptions.Exit(1)
    catalog: BackupCatalog | None = None
    try:
        project = _backup_commands.resolve_project_path(ctx)
        catalog = _backup_commands._catalog()
        _client, resource = _backup_commands._backup_resource(catalog)

        def confirm() -> None:
            click.confirm("Delete eligible retained backups?", default=False, abort=True)

        status, value = run_or_preview(
            lambda: resource.prune_command(project, dry_run=dry_run),
            command_name="backup.prune",
            mode=mode,
            dry_run=dry_run,
            result=lambda item: model_to_dict(cast("BackupPruneResult", item)),
            confirm=None if dry_run or yes else confirm,
            emit_normal=False,
            rich=_rich_prune,
        )
        if not dry_run:
            assert value is not None
            emit(
                success_document(command="backup.prune", result=model_to_dict(value)),
                mode,
                rich=_rich_prune,
            )
        if status:
            return
    except Exception as exc:
        fail(mode, "backup.prune", exc, dry_run=dry_run)
    finally:
        if catalog is not None:
            catalog.close()
