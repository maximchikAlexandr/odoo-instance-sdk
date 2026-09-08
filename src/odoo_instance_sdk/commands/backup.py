"""Bounded backup catalogue commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands.context import CliContext, pass_cli_context, resolve_catalogue_scope
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    emit,
    fail,
    failure_document,
    model_to_dict,
    output_options,
    resolve_output_mode,
    rich_print,
    run_or_preview,
    success_document,
)
from odoo_instance_sdk.exceptions import BackupValidationUnavailableError
from odoo_instance_sdk.internal.urls import normalize_base_url
from odoo_instance_sdk.models import (
    BackupDeletionResult,
    BackupEventType,
    BackupValidationStatus,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.resources.backup import BackupResource
    from odoo_instance_sdk.storage.backup_catalog import (
        BackupCatalog,
        BackupProjection,
        BackupProjectionPage,
    )


class _CatalogPathProvider:
    provider: Callable[[], Path] | None = None


_catalog_path_provider = _CatalogPathProvider()


def configure_catalog_path_provider(provider: Callable[[], Path]) -> None:
    """Bind catalogue access to the CLI's imported path seam."""
    _catalog_path_provider.provider = provider


def _catalog() -> BackupCatalog:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    if _catalog_path_provider.provider is None:  # pragma: no cover - CLI always configures this
        from odoo_instance_sdk.internal.paths import get_catalog_path

        return BackupCatalog(db_path=get_catalog_path())
    return BackupCatalog(db_path=_catalog_path_provider.provider())


def _backup_payload(projection: BackupProjection) -> JsonObject:
    backup = model_to_dict(projection.backup)
    return {
        **backup,
        "state": projection.state.value,
        "catalogue_time": projection.catalogue_time.isoformat(),
        "file_present": projection.file_present,
        "recorded_bytes": projection.recorded_bytes,
        "occupied_bytes": projection.occupied_bytes,
        "history": [model_to_dict(event) for event in projection.history],
        "restore_links": [
            {
                "db_host": link.db_host,
                "db_port": link.db_port,
                "database_name": link.database_name,
                "restored_at": link.restored_at.isoformat(),
            }
            for link in projection.restore_links
        ],
        "environment_links": [
            {
                "environment_id": link.environment_id,
                "name": link.name,
                "state": link.state,
                "target_database": link.target_database,
            }
            for link in projection.environment_links
        ],
    }


def _page_payload(page: BackupProjectionPage) -> JsonObject:
    return {
        "backups": [_backup_payload(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


def _rich_table(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    backups = result.get("backups", [])
    if not isinstance(backups, list):
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    table = Table("UUID", "Source", "Database", "State", "File", "Bytes", "Catalogue time")
    for item in backups:
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(item.get("id", "")),
            str(item.get("source_base_url", "")),
            str(item.get("database_name", "")),
            str(item.get("state", "")),
            str(item.get("file_present", False)).lower(),
            str(item.get("recorded_bytes", "")),
            str(item.get("catalogue_time", "")),
        )
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    next_cursor = result.get("next_cursor")
    if next_cursor:
        console.print(f"Next cursor: {next_cursor}")
    return console.export_text().rstrip()


def _rich_detail(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    return json.dumps(document.result, ensure_ascii=False, default=str, indent=2, sort_keys=True)


def _rich_delete(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    if "plan" in result:
        return "Delete plan:\n" + json.dumps(
            result["plan"], ensure_ascii=False, default=str, indent=2, sort_keys=True
        )
    return json.dumps(result, ensure_ascii=False, default=str, indent=2, sort_keys=True)


def _validation_status(projection: BackupProjection) -> BackupValidationStatus | None:
    for event in reversed(projection.history):
        if event.event_type is BackupEventType.VALIDATION_SUCCEEDED:
            return BackupValidationStatus.VALID
        if event.event_type is BackupEventType.VALIDATION_FAILED:
            return BackupValidationStatus.INVALID
        if event.event_type is BackupEventType.VALIDATION_UNAVAILABLE:
            return BackupValidationStatus.UNAVAILABLE
    return None


@click.group("backup", help="Inspect and manage retained backups.")
def backup_group() -> None:
    """Inspect and manage retained backups."""


@backup_group.command("list", help="List retained backup catalogue records.")
@click.option("--source", "source_base_url", default=None, help="Filter by source base URL.")
@click.option("--database", "database_name", default=None, help="Filter by database name.")
@click.option(
    "--all", "include_all_states", is_flag=True, default=False, help="Include all states."
)
@click.option("--all-projects", is_flag=True, default=False, help="List all project-owned records.")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--cursor", default=None, help="Opaque cursor returned by a previous page.")
@output_options
@pass_cli_context
def backup_list(
    ctx: CliContext,
    source_base_url: str | None,
    database_name: str | None,
    include_all_states: bool,
    all_projects: bool,
    limit: int,
    cursor: str | None,
    output_format: str | None,
    json_output: bool,
) -> None:
    """List state-aware backup projections for the current project."""
    mode = resolve_output_mode(output_format, json_output)
    catalog: BackupCatalog | None = None
    try:
        project_id, project_source = resolve_catalogue_scope(ctx, all_projects)
        catalog = _catalog()
        if source_base_url is not None:
            source_base_url = normalize_base_url(source_base_url)
        page = catalog._list_backup_projections(
            source_base_url=source_base_url,
            database_name=database_name,
            include_all_states=include_all_states,
            project_id=project_id,
            limit=limit,
            cursor=cursor,
        )
        emit(
            success_document(
                command="backup.list",
                result=_page_payload(page),
                provenance={"project_source": project_source, "environment_source": "null"},
            ),
            mode,
            rich=_rich_table,
        )
    except Exception as exc:
        fail(mode, "backup.list", exc)
    finally:
        if catalog is not None:
            catalog.close()


@backup_group.command("show", help="Show one retained backup by its complete UUID.")
@click.argument("backup_id")
@output_options
def backup_show(backup_id: str, output_format: str | None, json_output: bool) -> None:
    """Show one complete state-aware backup projection."""
    mode = resolve_output_mode(output_format, json_output)
    catalog: BackupCatalog | None = None
    try:
        catalog = _catalog()
        projection = catalog._resolve_backup_projection(backup_id)
        emit(
            success_document(command="backup.show", result=_backup_payload(projection)),
            mode,
            rich=_rich_detail,
        )
    except Exception as exc:
        fail(mode, "backup.show", exc)
    finally:
        if catalog is not None:
            catalog.close()


@backup_group.command("validate", help="Validate one retained backup by its complete UUID.")
@click.argument("backup_id")
@output_options
def backup_validate(backup_id: str, output_format: str | None, json_output: bool) -> None:
    """Validate an exact backup and distinguish invalid from unavailable."""
    mode = resolve_output_mode(output_format, json_output)
    catalog: BackupCatalog | None = None
    try:
        catalog = _catalog()
        projection = catalog._resolve_backup_projection(backup_id)
        _client, resource = _backup_resource(catalog)
        command = resource.validate_command(projection.backup, raise_if_unavailable=False)
        _status, validation = run_or_preview(
            lambda: command,
            command_name="backup.validate",
            mode=mode,
            dry_run=False,
            emit_normal=False,
        )
        if validation is None:
            raise RuntimeError("backup validation returned no result")  # noqa: TRY301
        payload = model_to_dict(validation)
        refreshed = catalog._resolve_backup_projection(backup_id)
        validation_status = _validation_status(refreshed)
        if validation_status is BackupValidationStatus.INVALID:
            emit(
                failure_document(
                    command="backup.validate",
                    error_code="backup_validate_invalid",
                    error_message="Backup archive is invalid",
                    error_details=payload,
                ),
                mode,
                rich=_rich_detail,
            )
            raise click.exceptions.Exit(1)  # noqa: TRY301
        if validation_status is BackupValidationStatus.UNAVAILABLE:
            emit(
                failure_document(
                    command="backup.validate",
                    error_code="backup_validate_unavailable",
                    error_message="Backup validator is unavailable",
                    error_details=payload,
                ),
                mode,
                rich=_rich_detail,
            )
            raise click.exceptions.Exit(1)  # noqa: TRY301
        emit(success_document(command="backup.validate", result=payload), mode, rich=_rich_detail)
    except click.exceptions.Exit:
        raise
    except BackupValidationUnavailableError as exc:
        fail(mode, "backup.validate", exc, error_code="backup_validate_unavailable")
    except Exception as exc:
        fail(mode, "backup.validate", exc)
    finally:
        if catalog is not None:
            catalog.close()


def _backup_resource(catalog: BackupCatalog) -> tuple[OdooClient, BackupResource]:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    client._catalog = catalog
    return client, client.backups


@backup_group.command("delete", help="Delete one retained backup by its complete UUID.")
@click.argument("backup_id")
@click.option("--dry-run", is_flag=True, default=False, help="Show the immutable deletion plan.")
@click.option("--yes", is_flag=True, default=False, help="Skip interactive confirmation.")
@output_options
def backup_delete(
    backup_id: str,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Delete one exact UUID with an immutable preview and confirmation gate."""
    mode = resolve_output_mode(output_format, json_output)
    if not dry_run and not yes and mode is not OutputMode.RICH:
        emit(
            failure_document(
                command="backup.delete",
                error_code="confirmation_required",
                error_message="backup delete requires --yes in machine output mode",
            ),
            mode,
        )
        raise click.exceptions.Exit(1)

    catalog: BackupCatalog | None = None
    try:
        catalog = _catalog()
        projection = catalog._resolve_backup_projection(backup_id)
        plan = {
            "operation": "delete",
            "backup_id": str(projection.backup.id),
            "path": projection.backup.path,
            "filename": projection.backup.filename,
            "state": projection.state.value,
            "file_present": projection.file_present,
            "recorded_bytes": projection.recorded_bytes,
            "restore_links": _backup_payload(projection)["restore_links"],
            "environment_links": _backup_payload(projection)["environment_links"],
        }
        if dry_run:
            emit(
                success_document(command="backup.delete", result={"plan": plan}, dry_run=True),
                mode,
                rich=_rich_delete,
            )
            return

        _client, resource = _backup_resource(catalog)
        command = resource.delete_command(projection.backup)

        def confirm() -> None:
            rich_print(
                _rich_delete(success_document(command="backup.delete", result={"plan": plan})),
                preserve_newlines=True,
            )
            click.confirm(f"Delete backup {projection.backup.id}?", default=False, abort=True)

        status, _result = run_or_preview(
            lambda: command,
            command_name="backup.delete",
            mode=mode,
            dry_run=False,
            result=lambda value: model_to_dict(cast("BackupDeletionResult", value)),
            confirm=None if yes else confirm,
            rich=_rich_delete,
        )
        raise click.exceptions.Exit(status)  # noqa: TRY301
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        fail(mode, "backup.delete", exc)
    finally:
        if catalog is not None:
            catalog.close()


__all__ = ["backup_group", "configure_catalog_path_provider"]
