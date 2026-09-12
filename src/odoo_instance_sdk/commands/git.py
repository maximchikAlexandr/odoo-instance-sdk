"""CLI adapters for the concrete Odoo Git workflow resource."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click

else:
    import rich_click as click

from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.models import GitCheckResult
from odoo_instance_sdk.resources.git import GitResource


def _rich(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    value = document.result if isinstance(document.result, dict) else {}
    table = Table("Field", "Value", title=f"Git {document.command.rsplit('.', 1)[-1]}")
    for key, item in value.items():
        rendered = (
            json.dumps(item, ensure_ascii=False, default=str)
            if isinstance(item, (dict, list))
            else str(item)
        )
        table.add_row(str(key), rendered)
    console = Console(record=True, color_system=None, width=180)
    console.print(table)
    return console.export_text().rstrip()


def _resource(ctx: CliContext) -> GitResource:
    instance = cli_context.ready_instance(ctx).instance
    resource = getattr(instance, "git", None)
    if not isinstance(resource, GitResource):
        raise TypeError("resolved instance has no Git resource")
    return resource


def _error_code(error: BaseException) -> str | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


@click.group("git", help="Generate and safely synchronize Odoo Git workflows.")
def git_group() -> None:
    pass


@git_group.command("commit", help="Create one staged Odoo commit.")
@click.argument("description")
@click.option("--ticket", default=None, help="Tracker-neutral Ticket key.")
@click.option("--tag", default=None, help="Explicit uppercase commit prefix.")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@output_options
@pass_cli_context
def git_commit(
    ctx: CliContext,
    description: str,
    ticket: str | None,
    tag: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        resource = _resource(ctx)
        status, _ = run_or_preview(
            lambda: resource.commit_command(description=description, ticket=ticket, tag=tag),
            command_name="git.commit",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value is not None else {},
            confirm=None
            if yes
            else lambda: fail(
                mode,
                "git.commit",
                "git commit requires --yes",
                dry_run=False,
                error_code="confirmation_required",
            ),
            rich=_rich,
        )
    except SystemExit:
        raise
    except Exception as error:
        fail(mode, "git.commit", error, dry_run=dry_run, error_code=_error_code(error))
    raise click.exceptions.Exit(status)


@git_group.command("check", help="Validate Odoo commit history.")
@click.option("--base", default=None, help="Base ref (then configured environment/project base).")
@output_options
@pass_cli_context
def git_check(
    ctx: CliContext,
    base: str | None,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        resource = _resource(ctx)
        status, value = run_or_preview(
            lambda: resource.check_command(base=base),
            command_name="git.check",
            mode=mode,
            dry_run=False,
            result=lambda result: model_to_dict(result) if result is not None else {},
            rich=_rich,
        )
        if isinstance(value, GitCheckResult) and not value.valid:
            status = 1
    except SystemExit:
        raise
    except Exception as error:
        fail(mode, "git.check", error, dry_run=False, error_code=_error_code(error))
    raise click.exceptions.Exit(status)


@git_group.command("absorb", help="Absorb staged hunks with optional git-absorb.")
@click.option("--base", default=None)
@click.option("--and-rebase", is_flag=True, help="Pass --and-rebase to git-absorb.")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@output_options
@pass_cli_context
def git_absorb(
    ctx: CliContext,
    base: str | None,
    and_rebase: bool,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        resource = _resource(ctx)
        status, _ = run_or_preview(
            lambda: resource.absorb_command(base=base, dry_run=dry_run, and_rebase=and_rebase),
            command_name="git.absorb",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value is not None else {},
            confirm=None
            if yes
            else lambda: fail(
                mode,
                "git.absorb",
                "git absorb requires --yes",
                dry_run=False,
                error_code="confirmation_required",
            ),
            rich=_rich,
        )
    except SystemExit:
        raise
    except Exception as error:
        fail(mode, "git.absorb", error, dry_run=dry_run, error_code=_error_code(error))
    raise click.exceptions.Exit(status)


@git_group.command("sync", help="Fetch, rebase, validate, and optionally publish a branch.")
@click.option("--base", default=None)
@click.option("--push", is_flag=True, help="Publish to the same-name origin branch.")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@output_options
@pass_cli_context
def git_sync(
    ctx: CliContext,
    base: str | None,
    push: bool,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        resource = _resource(ctx)
        status, _ = run_or_preview(
            lambda: resource.sync_command(base=base, push=push),
            command_name="git.sync",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value is not None else {},
            confirm=None
            if yes
            else lambda: fail(
                mode,
                "git.sync",
                "git sync requires --yes",
                dry_run=False,
                error_code="confirmation_required",
            ),
            rich=_rich,
        )
    except SystemExit:
        raise
    except Exception as error:
        fail(mode, "git.sync", error, dry_run=dry_run, error_code=_error_code(error))
    raise click.exceptions.Exit(status)


def register_git_commands(group: click.Group) -> None:
    group.add_command(git_group)


__all__ = ["git_group", "register_git_commands"]
