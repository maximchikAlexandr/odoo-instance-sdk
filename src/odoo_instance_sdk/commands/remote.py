"""Thin CLI projections for named project remote sources."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.commands.context import CliContext, pass_cli_context, resolve_project_path
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    _InspectableCommand,
    emit,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    success_document,
)
from odoo_instance_sdk.project import RemoteSourceConfig, normalize_remote_name
from odoo_instance_sdk.project_init import (
    configure_remote_source_command,
    list_remote_sources,
    remove_remote_source_command,
)

type _RemoteSourceTuple = tuple[RemoteSourceConfig, ...]


def _source_payload(source: RemoteSourceConfig) -> JsonObject:
    return model_to_dict(source)


def _sources_payload(sources: tuple[RemoteSourceConfig, ...]) -> JsonObject:
    return {"remote_instances": [_source_payload(source) for source in sources]}


def _rich_sources(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    sources = result.get("remote_instances", [])
    if not isinstance(sources, list) or not sources:
        return "No named remote sources configured."
    return "\n".join(
        f"{item.get('name')}: {item.get('base_url')} / {item.get('database')} / {item.get('git_branch')}"
        for item in sources
        if isinstance(item, dict)
    )


@click.group("remote", help="Manage named remote backup sources.")
def remote_group() -> None:
    """Manage named remote backup sources."""


@remote_group.command("ls", aliases=["list"], help="List configured named remote sources.")
@output_options
@pass_cli_context
def remote_list(ctx: CliContext, output_format: str | None, json_output: bool) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        project = resolve_project_path(ctx)
        result = _sources_payload(list_remote_sources(project))
        emit(
            success_document(command="remote.list", result=result),
            mode,
            rich=_rich_sources,
        )
    except Exception as exc:
        fail(mode, "remote.list", exc, dry_run=False)


def _run_remote_update(
    *,
    command: _InspectableCommand[tuple[RemoteSourceConfig, ...]],
    command_name: str,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        status, value = run_or_preview(
            lambda: command,
            command_name=command_name,
            mode=mode,
            dry_run=dry_run,
            result=lambda sources: _sources_payload(
                cast("tuple[RemoteSourceConfig, ...]", sources)
            ),
            emit_normal=False,
            rich=_rich_sources,
        )
        if dry_run:
            return
        assert value is not None
        emit(
            success_document(
                command=command_name,
                result=_sources_payload(value),
            ),
            mode,
            rich=_rich_sources,
        )
        if status:
            return
    except Exception as exc:
        fail(mode, command_name, exc, dry_run=dry_run)


@remote_group.command("add", help="Add a named remote source.")
@click.argument("name")
@click.option("--url", "base_url", required=True, help="Remote Odoo base URL.")
@click.option("--database", required=True, help="Remote database name.")
@click.option("--branch", "git_branch", required=True, help="Declared source Git branch.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the manifest change only.")
@output_options
@pass_cli_context
def remote_add(
    ctx: CliContext,
    name: str,
    base_url: str,
    database: str,
    git_branch: str,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        command = configure_remote_source_command(
            resolve_project_path(ctx),
            RemoteSourceConfig(
                name=name, base_url=base_url, database=database, git_branch=git_branch
            ),
            replace=False,
        )
    except Exception as exc:
        fail(mode, "remote.add", exc, dry_run=dry_run)
    _run_remote_update(
        command_name="remote.add",
        command=command,
        dry_run=dry_run,
        output_format=output_format,
        json_output=json_output,
    )


@remote_group.command("update", help="Update an existing named remote source.")
@click.argument("name")
@click.option("--url", "base_url", default=None, help="Remote Odoo base URL.")
@click.option("--database", default=None, help="Remote database name.")
@click.option("--branch", "git_branch", default=None, help="Declared source Git branch.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the manifest change only.")
@output_options
@pass_cli_context
def remote_update(
    ctx: CliContext,
    name: str,
    base_url: str | None,
    database: str | None,
    git_branch: str | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        project = resolve_project_path(ctx)
        normalized_name = normalize_remote_name(name)
        existing = {source.name: source for source in list_remote_sources(project)}.get(
            normalized_name
        )
        if existing is None:

            def missing_source() -> None:
                raise click.UsageError(f"unknown remote source {name!r}")  # noqa: TRY301

            missing_source()
        assert existing is not None
        command = configure_remote_source_command(
            project,
            RemoteSourceConfig(
                name=existing.name,
                base_url=existing.base_url if base_url is None else base_url,
                database=existing.database if database is None else database,
                git_branch=existing.git_branch if git_branch is None else git_branch,
            ),
            replace=True,
        )
    except Exception as exc:
        fail(mode, "remote.update", exc, dry_run=dry_run)
    _run_remote_update(
        command_name="remote.update",
        command=command,
        dry_run=dry_run,
        output_format=output_format,
        json_output=json_output,
    )


@remote_group.command("remove", help="Remove a named remote source configuration.")
@click.argument("name")
@click.option("--dry-run", is_flag=True, default=False, help="Show the manifest change only.")
@output_options
@pass_cli_context
def remote_remove(
    ctx: CliContext,
    name: str,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        command = remove_remote_source_command(resolve_project_path(ctx), name)
    except Exception as exc:
        fail(mode, "remote.remove", exc, dry_run=dry_run)
    _run_remote_update(
        command_name="remote.remove",
        command=command,
        dry_run=dry_run,
        output_format=output_format,
        json_output=json_output,
    )
