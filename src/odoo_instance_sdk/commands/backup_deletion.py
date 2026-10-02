"""Multi-target backup deletion projections."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.commands import backup as _backup_commands
from odoo_instance_sdk.commands.backup import _backup_payload, backup_group
from odoo_instance_sdk.commands.multi_target import run_multi_target_deletion
from odoo_instance_sdk.commands.output import (
    JsonObject,
    JsonValue,
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
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.models import BackupDeletionResult

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, BackupProjection


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
                    _backup_commands._rich_delete(
                        success_document(command="backup.delete", result={"plan": plan})
                    )
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
            catalog = _backup_commands._catalog()
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
                    rich=_backup_commands._rich_delete,
                )
                return
            _client, resource = _backup_commands._backup_resource(catalog)
            command = resource.delete_command(projection.backup)

            def confirm() -> None:
                rich_print(
                    _backup_commands._rich_delete(
                        success_document(command="backup.delete", result={"plan": plan})
                    ),
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
                rich=_backup_commands._rich_delete,
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
            _client, resource = _backup_commands._backup_resource(catalog)
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
                _backup_commands._rich_delete(
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
        catalog = _backup_commands._catalog()
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
