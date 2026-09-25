"""Bounded backup catalogue commands."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

import msgspec

if TYPE_CHECKING:
    import click
    from rich.table import Table
else:
    import rich_click as click

from rich.console import Console  # noqa: I001 -- keep CLI format aliases grouped; remove when Ruff supports grouped aliases.
from rich.text import Text

from odoo_instance_sdk.commands.context import CliContext, pass_cli_context, resolve_catalogue_scope
from odoo_instance_sdk.commands.multi_target import run_multi_target_deletion
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    bordered_table,
    emit,
    fail,
    failure_document,
    field_schema,
    model_to_dict,
    output_options,
    render_rich_text,
    resolve_output_mode,
    rich_print,
    run_or_preview,
    success_document,
)
from odoo_instance_sdk.exceptions import BackupValidationUnavailableError
from odoo_instance_sdk.internal.cli_format import (
    human_bytes as _human_bytes,
    rich_cell,
    rich_local_time,
)
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.urls import normalize_base_url
from odoo_instance_sdk.models import (
    BackupDeletionResult,
    BackupEvent,
    BackupEventType,
    BackupFormat,
    BackupInspectResult,
    BackupState,
    BackupValidationResult,
    BackupValidationStatus,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.resources.backup import BackupResource
    from odoo_instance_sdk.storage.backup_catalog import (
        BackupCatalog,
        BackupProjection,
        BackupProjectionPage,
    )


class _CatalogPathProvider:
    provider: Callable[[], Path] | None = None


_catalog_path_provider = _CatalogPathProvider()


class _BackupRestoreLinkResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    db_host: str
    db_port: int
    database_name: str
    restored_at: datetime


class _BackupEnvironmentLinkResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    environment_id: str
    name: str
    state: str
    target_database: str | None


class _BackupPayloadResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The exact typed result emitted by backup show/list."""

    id: uuid.UUID
    source_base_url: str
    database_name: str
    format: BackupFormat
    filestore_requested: bool
    path: str
    filename: str
    size_bytes: int
    sha256: str
    downloaded_at: datetime
    source_git_branch: str | None
    state: BackupState
    catalogue_time: datetime
    file_present: bool
    recorded_bytes: int | None
    occupied_bytes: int | None
    history: tuple[BackupEvent, ...]
    restore_links: tuple[_BackupRestoreLinkResult, ...]
    environment_links: tuple[_BackupEnvironmentLinkResult, ...]


class _BackupListResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    backups: tuple[_BackupPayloadResult, ...]
    next_cursor: Annotated[str | None, "odcli-structural"]


def _rich_local_time_cell(value: JsonValue) -> Text:
    if not isinstance(value, str) or not value:
        return rich_cell("—")
    return Text(rich_local_time(value))


def configure_catalog_path_provider(provider: Callable[[], Path]) -> None:
    """Bind catalogue access to the CLI's imported path seam."""
    _catalog_path_provider.provider = provider


def _catalog() -> BackupCatalog:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    if _catalog_path_provider.provider is None:  # pragma: no cover - CLI always configures this
        from odoo_instance_sdk.internal.paths import get_catalog_path

        return BackupCatalog(db_path=get_catalog_path())
    return BackupCatalog(db_path=_catalog_path_provider.provider())


def _backup_payload(projection: BackupProjection) -> _BackupPayloadResult:
    backup = projection.backup
    return _BackupPayloadResult(
        id=backup.id,
        source_base_url=backup.source_base_url,
        database_name=backup.database_name,
        format=backup.format,
        filestore_requested=backup.filestore_requested,
        path=backup.path,
        filename=backup.filename,
        size_bytes=backup.size_bytes,
        sha256=backup.sha256,
        downloaded_at=backup.downloaded_at,
        source_git_branch=backup.source_git_branch,
        state=projection.state,
        catalogue_time=projection.catalogue_time,
        file_present=projection.file_present,
        recorded_bytes=projection.recorded_bytes,
        occupied_bytes=projection.occupied_bytes,
        history=projection.history,
        restore_links=tuple(
            _BackupRestoreLinkResult(
                db_host=link.db_host,
                db_port=link.db_port,
                database_name=link.database_name,
                restored_at=link.restored_at,
            )
            for link in projection.restore_links
        ),
        environment_links=tuple(
            _BackupEnvironmentLinkResult(
                environment_id=link.environment_id,
                name=link.name,
                state=link.state,
                target_database=link.target_database,
            )
            for link in projection.environment_links
        ),
    )


def _page_payload(page: BackupProjectionPage) -> _BackupListResult:
    return _BackupListResult(
        backups=tuple(_backup_payload(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


def _rich_table(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    backups = result.get("backups", [])
    if not isinstance(backups, list):
        return "No backups"
    table = bordered_table("UUID", "Source", "Database", "State", "File", "Bytes", "Catalogue time")
    if not backups:
        table.add_row("—", "—", "—", "—", "—", "—", "No backups")
    for item in backups:
        if not isinstance(item, dict):
            continue
        recorded_bytes = item.get("recorded_bytes")
        table.add_row(
            rich_cell(item.get("id", "")),
            rich_cell(item.get("source_base_url", "")),
            rich_cell(item.get("database_name", "")),
            rich_cell(item.get("state", "")),
            rich_cell(str(item.get("file_present", False)).lower()),
            rich_cell(
                _human_bytes(recorded_bytes)
                if isinstance(recorded_bytes, int) and not isinstance(recorded_bytes, bool)
                else "—"
            ),
            _rich_local_time_cell(item.get("catalogue_time")),
        )
    output = render_rich_text(table, width=Console().width)
    next_cursor = result.get("next_cursor")
    if next_cursor:
        output = (
            f"{output}\n"
            f"{render_rich_text(rich_cell(f'Next cursor: {next_cursor}'), width=Console().width)}"
        )
    return output


def _rich_summary_table(result: JsonObject) -> Table:
    summary = bordered_table("Field", "Value", title="Backup")
    for field in (
        "id",
        "source_base_url",
        "database_name",
        "state",
        "file_present",
        "recorded_bytes",
        "occupied_bytes",
        "catalogue_time",
    ):
        value = result.get(field)
        if field in {"recorded_bytes", "occupied_bytes"}:
            value = (
                _human_bytes(value)
                if isinstance(value, int) and not isinstance(value, bool)
                else "—"
            )
        if field == "catalogue_time" and isinstance(value, str):
            value = rich_local_time(value)
        summary.add_row(
            field.replace("_", " ").title(),
            rich_cell(value if value is not None else "—"),
        )
    return summary


def _rich_history_table(items: list[JsonValue]) -> Table:
    history = bordered_table("Seq", "Event", "Occurred", "Message", title="History")
    for item in items:
        if not isinstance(item, dict):
            continue
        history.add_row(
            rich_cell(item.get("sequence", "")),
            rich_cell(item.get("event_type", "")),
            _rich_local_time_cell(item.get("occurred_at")),
            rich_cell(item.get("message") or ""),
        )
    return history


def _rich_restore_table(items: list[JsonValue]) -> Table:
    restore = bordered_table("Host", "Port", "Database", "Restored", title="Restore links")
    for item in items:
        if not isinstance(item, dict):
            continue
        restore.add_row(
            rich_cell(item.get("db_host", "")),
            rich_cell(item.get("db_port", "")),
            rich_cell(item.get("database_name", "")),
            _rich_local_time_cell(item.get("restored_at")),
        )
    return restore


def _rich_environment_table(items: list[JsonValue]) -> Table:
    details = bordered_table("Details", title="Environment links")
    for item in items:
        details.add_row(rich_cell(item))
    return details


def _rich_detail(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    rendered_sections = [render_rich_text(_rich_summary_table(result), width=Console().width)]
    sections: tuple[tuple[str, str, Callable[[list[JsonValue]], Table]], ...] = (
        ("history", "History", _rich_history_table),
        ("restore_links", "Restore links", _rich_restore_table),
        ("environment_links", "Environment links", _rich_environment_table),
    )
    for field, _title, builder in sections:
        items = result.get(field)
        if not isinstance(items, list) or not items:
            continue
        rendered_sections.append(render_rich_text(builder(items), width=Console().width))
    return "\n\n".join(rendered_sections)


def _rich_validation(document: OutputDocument) -> str:
    """Render the typed validation result without exposing machine syntax."""
    result: JsonObject
    if document.ok:
        result = document.result if isinstance(document.result, dict) else {}
    else:
        details = document.error.details if document.error is not None else None
        result = details if details is not None else {}
    status = (
        "valid"
        if document.ok
        else (
            "invalid"
            if document.error is not None
            and document.error.code
            in ("backup_validate_invalid", "backup_corrupt", "backup_unsafe")
            else "unavailable"
        )
    )
    table = bordered_table("Field", "Value", title="Backup validation")
    table.add_row("Status", rich_cell(status))
    for field in ("db_name", "db_version"):
        if result.get(field) is not None:
            table.add_row(
                field.replace("_", " ").title(),
                rich_cell(result[field]),
            )
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        for error in errors:
            table.add_row("Error", rich_cell(error))
    elif not document.ok and document.error is not None:
        table.add_row("Error", rich_cell(document.error.message))
    terminal_width = Console().width
    return render_rich_text(table, width=terminal_width)


def _emit_validation_failure(document: OutputDocument, mode: OutputMode) -> None:
    if mode is OutputMode.RICH:
        Console(stderr=True).print(_rich_validation(document), markup=False, soft_wrap=True)
    else:
        emit(document, mode, rich=_rich_validation)


def _rich_delete(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    plan = result.get("plan")
    if isinstance(plan, dict):
        table = bordered_table("Field", "Value", title="Delete plan")
        table.columns[1].overflow = "fold"
        for field in ("backup_id", "path", "state", "file_present"):
            if field in plan:
                table.add_row(field.replace("_", " ").title(), rich_cell(plan[field]))
        return f"Delete plan:\n{render_rich_text(table, width=Console().width)}"
    backup_id = result.get("backup_id", result.get("id", "unknown"))
    return f"Deleted backup {backup_id}."


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


@backup_group.command("ls", aliases=["list"], help="List retained backup catalogue records.")
@click.option("--source", "source_base_url", default=None, help="Filter by source base URL.")
@click.option("--database", "database_name", default=None, help="Filter by database name.")
@click.option(
    "--all", "include_all_states", is_flag=True, default=False, help="Include all states."
)
@click.option("--all-projects", is_flag=True, default=False, help="List all project-owned records.")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--cursor", default=None, help="Opaque cursor returned by a previous page.")
@output_options
@field_schema(_BackupListResult)
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
                result=model_to_dict(_page_payload(page)),
                provenance={"project_source": project_source, "environment_source": "null"},
            ),
            mode,
            rich=_rich_table,
        )
    except Exception as exc:
        fail(mode, "backup.list", exc, dry_run=False)
    finally:
        if catalog is not None:
            catalog.close()


@backup_group.command(
    "inspect", aliases=["show"], help="Show one retained backup by its complete UUID."
)
@click.argument("backup_id")
@output_options
@field_schema(_BackupPayloadResult)
def backup_show(backup_id: str, output_format: str | None, json_output: bool) -> None:
    """Show one complete state-aware backup projection."""
    mode = resolve_output_mode(output_format, json_output)
    catalog: BackupCatalog | None = None
    try:
        catalog = _catalog()
        _client, resource = _backup_resource(catalog)
        status, _result = run_or_preview(
            lambda: resource.inspect_command(backup_id),
            command_name="backup.show",
            mode=mode,
            dry_run=False,
            result=cast(
                "Callable[[BackupInspectResult | None], dict[str, JsonValue]]", model_to_dict
            ),
            rich=_rich_detail,
        )
    except Exception as exc:
        fail(mode, "backup.show", exc, dry_run=False)
        return
    finally:
        if catalog is not None:
            catalog.close()
    raise click.exceptions.Exit(status)


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
            structural_code = (
                validation.error_code
                if isinstance(validation, BackupValidationResult) and validation.error_code
                else "backup_validate_invalid"
            )
            _emit_validation_failure(
                failure_document(
                    command="backup.validate",
                    dry_run=False,
                    error_code=structural_code,
                    error_message="Backup archive is invalid",
                    error_details=payload,
                ),
                mode,
            )
            raise click.exceptions.Exit(1)  # noqa: TRY301
        if validation_status is BackupValidationStatus.UNAVAILABLE:
            _emit_validation_failure(
                failure_document(
                    command="backup.validate",
                    dry_run=False,
                    error_code="backup_validate_unavailable",
                    error_message="Backup validator is unavailable",
                    error_details=payload,
                ),
                mode,
            )
            raise click.exceptions.Exit(1)  # noqa: TRY301
        emit(
            success_document(command="backup.validate", result=payload), mode, rich=_rich_validation
        )
    except click.exceptions.Exit:
        raise
    except BackupValidationUnavailableError as exc:
        fail(
            mode,
            "backup.validate",
            exc,
            dry_run=False,
            error_code="backup_validate_unavailable",
        )
    except Exception as exc:
        fail(mode, "backup.validate", exc, dry_run=False)
    finally:
        if catalog is not None:
            catalog.close()


def _backup_resource(catalog: BackupCatalog) -> tuple[OdooClient, BackupResource]:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    client._catalog = catalog
    return client, client.backups


def _dedup_targets(targets: Sequence[str], *, label: str) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for target in targets:
        if target in seen:
            raise click.UsageError(f"duplicate {label} {target!r}")
        seen.add(target)
        ordered.append(target)
    return tuple(ordered)


def _rich_multi_delete(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    targets = result.get("targets")
    if not isinstance(targets, list):
        return "Dry-run: no targets." if document.dry_run else "Deleted backups."
    if document.dry_run:
        return _rich_multi_dry_run(targets)
    return _rich_multi_result(targets)


def _rich_multi_dry_run(targets: list[JsonValue]) -> str:
    lines: list[str] = []
    for entry in targets:
        if isinstance(entry, dict):
            plan = entry.get("plan")
            if isinstance(plan, dict):
                lines.append(
                    _rich_delete(success_document(command="backup.delete", result={"plan": plan}))
                )
    return "\n".join(lines).rstrip() or "Dry-run: no targets."


def _rich_multi_result(targets: list[JsonValue]) -> str:
    lines: list[str] = []
    for entry in targets:
        if not isinstance(entry, dict):
            continue
        target = entry.get("target", "unknown")
        if entry.get("ok"):
            lines.append(f"Deleted backup {target}.")
        else:
            err = entry.get("error", "failed")
            lines.append(f"Failed to delete backup {target}: {err}")
    return "\n".join(lines) or "Deleted backups."


@backup_group.command(
    "rm",
    aliases=["delete"],
    help="Delete one or more retained backups by their complete UUIDs.",
)
@click.argument("backup_ids", nargs=-1, required=False)
@click.option("--dry-run", is_flag=True, default=False, help="Show the immutable deletion plan.")
@click.option("--yes", is_flag=True, default=False, help="Skip interactive confirmation.")
@output_options
def backup_delete(
    backup_ids: tuple[str, ...],
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Delete one or more exact UUIDs with one preview and confirmation gate."""
    mode = resolve_output_mode(output_format, json_output)
    if not backup_ids:
        raise click.UsageError("backup rm requires at least one BACKUP_UUID")
    if len(backup_ids) == 1:
        backup_id = backup_ids[0]
        if not dry_run and not yes and mode is not OutputMode.RICH:
            emit(
                failure_document(
                    command="backup.delete",
                    dry_run=dry_run,
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
            backup_payload = model_to_dict(_backup_payload(projection))
            plan = {
                "operation": "delete",
                "backup_id": str(projection.backup.id),
                "path": projection.backup.path,
                "filename": projection.backup.filename,
                "state": projection.state.value,
                "file_present": projection.file_present,
                "recorded_bytes": projection.recorded_bytes,
                "restore_links": backup_payload["restore_links"],
                "environment_links": backup_payload["environment_links"],
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
            fail(mode, "backup.delete", exc, dry_run=dry_run)
        finally:
            if catalog is not None:
                catalog.close()
        return
    _backup_delete_multi(backup_ids, dry_run=dry_run, yes=yes, mode=mode)


def _backup_delete_plan(item: tuple[str, BackupProjection]) -> JsonObject:
    _id, projection = item
    backup_payload = model_to_dict(_backup_payload(projection))
    return {
        "operation": "delete",
        "backup_id": str(projection.backup.id),
        "path": projection.backup.path,
        "filename": projection.backup.filename,
        "state": projection.state.value,
        "file_present": projection.file_present,
        "recorded_bytes": projection.recorded_bytes,
        "restore_links": backup_payload["restore_links"],
        "environment_links": backup_payload["environment_links"],
    }


def _resolve_backup_projections(
    catalog: BackupCatalog,
    targets: tuple[str, ...],
    *,
    mode: OutputMode,
    dry_run: bool,
) -> list[tuple[str, BackupProjection]] | None:
    projections: list[tuple[str, BackupProjection]] = []
    for backup_id in targets:
        try:
            projection = catalog._resolve_backup_projection(backup_id)
        except Exception as exc:
            fail(mode, "backup.delete", f"{backup_id}: {exc}", dry_run=dry_run)
            return None
        projections.append((backup_id, projection))
    return projections


def _make_backup_execute_target(
    catalog: BackupCatalog,
) -> Callable[[tuple[str, BackupProjection]], tuple[bool, JsonObject, str | None]]:
    def execute_target(
        item: tuple[str, BackupProjection],
    ) -> tuple[bool, JsonObject, str | None]:
        backup_id, _projection = item
        try:
            refreshed = catalog._resolve_backup_projection(backup_id)
            _client, resource = _backup_resource(catalog)
            command = resource.delete_command(refreshed.backup)
            result = command.run()
        except Exception as exc:
            return False, {}, sanitize_last_error(str(exc))
        return True, model_to_dict(result), None

    return execute_target


def _make_backup_confirm_prompt(
    build_plan: Callable[[tuple[str, BackupProjection]], JsonObject],
) -> Callable[[Sequence[tuple[str, BackupProjection]]], None]:
    def confirm_prompt(items: Sequence[tuple[str, BackupProjection]]) -> None:
        for backup_id, projection in items:
            rich_print(
                _rich_delete(
                    success_document(
                        command="backup.delete",
                        result={"plan": build_plan((backup_id, projection))},
                    )
                ),
                preserve_newlines=True,
            )
        click.confirm(f"Delete {len(items)} backup(s)?", default=False, abort=True)

    return confirm_prompt


def _backup_delete_multi(
    backup_ids: tuple[str, ...], *, dry_run: bool, yes: bool, mode: OutputMode
) -> None:
    targets = _dedup_targets(backup_ids, label="backup UUID")
    if not dry_run and not yes and mode is not OutputMode.RICH:
        emit(
            failure_document(
                command="backup.delete",
                dry_run=dry_run,
                error_code="confirmation_required",
                error_message="backup delete requires --yes in machine output mode",
            ),
            mode,
        )
        raise click.exceptions.Exit(1)

    catalog: BackupCatalog | None = None
    try:
        catalog = _catalog()
        projections = _resolve_backup_projections(catalog, targets, mode=mode, dry_run=dry_run)
        if projections is None:
            return

        run_multi_target_deletion(
            tuple(projections),
            command="backup.delete",
            mode=mode,
            dry_run=dry_run,
            yes=yes,
            target_id=lambda item: str(item[1].backup.id),
            target_label=lambda item: f"backup {item[1].backup.id} ({item[1].backup.filename})",
            build_plan=_backup_delete_plan,
            execute_target=_make_backup_execute_target(catalog),
            rich_summary=_rich_multi_delete,
            confirm_prompt=_make_backup_confirm_prompt(_backup_delete_plan),
        )
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        fail(mode, "backup.delete", exc, dry_run=dry_run)
    finally:
        if catalog is not None:
            catalog.close()


__all__ = ["backup_group", "configure_catalog_path_provider"]
