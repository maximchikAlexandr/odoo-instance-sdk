"""CLI adapters for the concrete Odoo Git workflow resource."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.resources.instance import OdooInstance

else:
    import rich_click as click

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    bordered_table,
    fail,
    model_to_dict,
    output_options,
    render_rich_text,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.config import InstanceConfig
from odoo_instance_sdk.models import GitCheckResult, StartConfig
from odoo_instance_sdk.resources.git import GitResource


def _rich(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    value = document.result if isinstance(document.result, dict) else {}
    table = bordered_table("Field", "Value", title=f"Git {document.command.rsplit('.', 1)[-1]}")
    for key, item in value.items():
        rendered = (
            json.dumps(item, ensure_ascii=False, default=str)
            if isinstance(item, (dict, list))
            else str(item)
        )
        table.add_row(str(key), rendered)
    return render_rich_text(table)


def _resource(ctx: CliContext) -> GitResource:
    instance = cli_context.ready_instance(ctx).instance
    resource = getattr(instance, "git", None)
    if not isinstance(resource, GitResource):
        raise TypeError("resolved instance has no Git resource")
    return resource


def _generic_resource(repository: Path | str | None = None) -> GitResource:
    """Build a Git resource without resolving an OdCLI project or instance."""
    if repository is None:
        return GitResource(cast("OdooInstance", None))
    root = Path(repository).expanduser().resolve()
    instance = SimpleNamespace(
        config=InstanceConfig(
            base_url="http://127.0.0.1:8069",
            default_cwd=root,
            start_config=StartConfig(addons_path=["addons"]),
        )
    )
    from odoo_instance_sdk.resources.module import ModuleResource

    instance.modules = ModuleResource(cast("OdooInstance", instance))
    return GitResource(cast("OdooInstance", instance))


def _error_code(error: BaseException) -> str | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


@click.group("git", help="Generate and safely synchronize Odoo Git workflows.")
def git_group() -> None:
    pass


_MEMBER_ID_HELP = (
    "From `multica workspace member list --output json`, use the person's `user_id`, "
    "not the membership-row `id`, agent ID, or squad ID."
)


@git_group.command(
    "commit",
    help="Create one staged Odoo commit; registered native checkouts need no local manifest, and --tag keeps the explicit Odoo prefix.",
)
@click.argument("description")
@click.option("--ticket", default=None, help="Tracker-neutral Ticket key.")
@click.option("--tag", default=None, help="Explicit uppercase commit prefix.")
@click.option("--repo", default=None, type=click.Path(), help="Explicit Git repository root.")
@click.option(
    "--member-id",
    default=None,
    help=f"{_MEMBER_ID_HELP} Selects project identity, or GitLab mode with --repo.",
)
@click.option("--dry-run", is_flag=True, help="Plan only.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@output_options
@pass_cli_context
def git_commit(
    ctx: CliContext,
    description: str,
    ticket: str | None,
    tag: str | None,
    repo: str | None,
    member_id: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    if repo is not None and member_id is None:
        raise click.UsageError("--repo requires --member-id")
    if repo is not None and tag is not None:
        raise click.UsageError("--tag is valid only for project-aware Odoo commits")
    try:
        resource = _generic_resource(repo) if repo is not None else _resource(ctx)
        status, _ = run_or_preview(
            lambda: (
                resource.commit_command(
                    description=description,
                    repository=repo,
                    member_id=member_id,
                    ticket=ticket,
                    tag=tag,
                )
                if member_id is not None
                else resource.commit_command(description=description, ticket=ticket, tag=tag)
            ),
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
@click.option("--repo", default=None, type=click.Path(), help="Explicit Git repository root.")
@click.option("--member-id", default=None, help=_MEMBER_ID_HELP)
@click.option("--push", is_flag=True, help="Publish to the same-name origin branch.")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@output_options
@pass_cli_context
def git_sync(
    ctx: CliContext,
    base: str | None,
    repo: str | None,
    member_id: str | None,
    push: bool,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    if (repo is None) != (member_id is None):
        raise click.UsageError("--repo and --member-id must be supplied together")
    if repo is not None and base is None:
        raise click.UsageError("explicit repository sync requires --base")
    try:
        resource = _generic_resource(repo) if repo is not None else _resource(ctx)
        status, _ = run_or_preview(
            lambda: (
                resource.sync_command(
                    repository=repo,
                    member_id=member_id,
                    base=cast("str", base),
                    push=push,
                )
                if repo is not None and member_id is not None
                else resource.sync_command(base=base, push=push)
            ),
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


@git_group.command("clone", help="Clone an allowed GitLab repository safely.")
@click.argument("url")
@click.argument("destination", type=click.Path())
@click.option("--member-id", required=True, help=_MEMBER_ID_HELP)
@click.option("--recurse-submodules", is_flag=True, help="Initialize validated missing submodules.")
@click.option("--branch", help="Branch to check out before initializing submodules.")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@output_options
def git_clone(
    url: str,
    destination: str,
    member_id: str,
    recurse_submodules: bool,
    branch: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        resource = _generic_resource()
        status, _ = run_or_preview(
            lambda: resource.clone_command(
                url,
                Path(destination),
                member_id=member_id,
                recurse_submodules=recurse_submodules,
                branch=branch,
            ),
            command_name="git.clone",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value is not None else {},
            confirm=None
            if yes
            else lambda: fail(
                mode,
                "git.clone",
                "git clone requires --yes",
                dry_run=False,
                error_code="confirmation_required",
            ),
            rich=_rich,
        )
    except SystemExit:
        raise
    except Exception as error:
        fail(mode, "git.clone", error, dry_run=dry_run, error_code=_error_code(error))
    raise click.exceptions.Exit(status)


@git_group.group("mr", help="Publish GitLab merge requests.")
def mr_group() -> None:
    pass


@mr_group.command("create", help="Create or update one exact GitLab merge request.")
@click.option("--repo", required=True, type=click.Path(), help="Explicit Git repository root.")
@click.option("--member-id", required=True, help=_MEMBER_ID_HELP)
@click.option("--target", required=True, help="Target branch.")
@click.option("--title", required=True, help="Merge-request title.")
@click.option("--description-file", required=True, type=click.Path(dir_okay=False))
@click.option("--image", default=None, type=click.Path(dir_okay=False))
@click.option("--dry-run", is_flag=True, help="Plan only.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@output_options
def git_mr_create(
    repo: str,
    member_id: str,
    target: str,
    title: str,
    description_file: str,
    image: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        resource = _generic_resource()
        status, _ = run_or_preview(
            lambda: resource.merge_request_command(
                repository=repo,
                member_id=member_id,
                target=target,
                title=title,
                description_file=description_file,
                image=image,
            ),
            command_name="git.mr.create",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value is not None else {},
            confirm=None
            if yes
            else lambda: fail(
                mode,
                "git.mr.create",
                "git merge-request create requires --yes",
                dry_run=False,
                error_code="confirmation_required",
            ),
            rich=_rich,
        )
    except SystemExit:
        raise
    except Exception as error:
        fail(mode, "git.mr.create", error, dry_run=dry_run, error_code=_error_code(error))
    raise click.exceptions.Exit(status)


def register_git_commands(group: click.Group) -> None:
    group.add_command(git_group)


__all__ = ["git_group", "register_git_commands"]
