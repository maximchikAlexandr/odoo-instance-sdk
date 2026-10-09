"""The bounded ``odcli-multica`` command surface."""

from __future__ import annotations

import os
from pathlib import Path

import click
from multica_py import ClientConfig, MulticaClient

from odcli_multica.client import MulticaOdooClient, PrepareCommand
from odcli_multica.models import (
    ContextRequest,
    PreparationRequest,
)
from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    OutputMode,
    emit,
    failure_document,
    model_to_dict,
    success_document,
)


def _mode(value: str) -> OutputMode:
    return OutputMode(value)


def _client(profile: str | None, workspace_id: str | None, odoo_bin: str) -> MulticaOdooClient:
    multica = MulticaClient(ClientConfig(profile=profile, workspace_id=workspace_id))
    core = OdooClient(config=OdooClientConfig(executable=odoo_bin))
    return MulticaOdooClient(core, multica)


def _client_or_failure(
    profile: str | None,
    workspace_id: str | None,
    odoo_bin: str,
    mode: OutputMode,
    command: str,
) -> MulticaOdooClient | None:
    try:
        return _client(profile, workspace_id, odoo_bin)
    except click.exceptions.Exit:
        raise
    except BaseException as error:
        _failure(mode, command, error)
        return None


def _request(
    checkout_path: Path,
    project: Path,
    multica_project: str,
    issue: str,
    run: str,
    repository_url: str,
) -> ContextRequest:
    return ContextRequest(
        checkout_path=checkout_path,
        core_project=project,
        multica_project=multica_project,
        issue=issue,
        run=run,
        repository_url=repository_url,
    )


def _finish(mode: OutputMode, document: OutputDocument) -> None:
    # The shared core emitter owns sanitization and the one-document boundary.
    status = emit(document, mode)
    if status:
        raise click.exceptions.Exit(status)


def _failure(mode: OutputMode, command: str, error: BaseException) -> None:
    _finish(
        mode,
        failure_document(
            command=command,
            dry_run=False,
            error_code="interrupted" if isinstance(error, KeyboardInterrupt) else None,
            error_message=error,
        ),
    )


def _run_context(
    client: MulticaOdooClient,
    request: ContextRequest,
    mode: OutputMode,
    dry_run: bool,
) -> None:
    try:
        command = client.context_command(request)
        if dry_run:
            _finish(
                mode,
                success_document(
                    command="context",
                    result=model_to_dict(command.plan),
                    dry_run=True,
                ),
            )
            return
        value = command.run()
        _finish(
            mode,
            success_document(command="context", result=model_to_dict(value)),
        )
    except BaseException as error:
        if isinstance(error, click.exceptions.Exit):
            raise
        _failure(mode, "context", error)


def _run_prepare(
    client: MulticaOdooClient,
    request: PreparationRequest,
    mode: OutputMode,
    dry_run: bool,
) -> None:
    try:
        command: PrepareCommand = client.prepare_command(request)
        if dry_run:
            _finish(
                mode,
                success_document(
                    command="env.prepare",
                    context=model_to_dict(command.context),
                    result=model_to_dict(command.plan),
                    dry_run=True,
                ),
            )
            return
        value = command.run()
        _finish(
            mode,
            success_document(
                command="env.prepare",
                context=model_to_dict(command.context),
                result=model_to_dict(value),
            ),
        )
    except BaseException as error:
        if isinstance(error, click.exceptions.Exit):
            raise
        _failure(mode, "env.prepare", error)


@click.group()
def cli() -> None:
    """Compose native Multica checkout facts with core Odoo preparation."""


@cli.command("context")
@click.argument("checkout_path", type=click.Path(path_type=Path))
@click.option("--project", "project", required=True, type=click.Path(path_type=Path))
@click.option("--multica-project", required=True)
@click.option("--issue", required=True)
@click.option("--run", "run_id", required=True)
@click.option("--repository-url", required=True)
@click.option("--profile", default=None)
@click.option("--workspace-id", default=None)
@click.option("--odoo-bin", default=lambda: os.environ.get("ODCLI_ODOO_BIN", "odoo"))
@click.option(
    "--format", "output_format", type=click.Choice(["rich", "json", "toon"]), default="rich"
)
@click.option("--dry-run", is_flag=True)
def context_command(
    checkout_path: Path,
    project: Path,
    multica_project: str,
    issue: str,
    run_id: str,
    repository_url: str,
    profile: str | None,
    workspace_id: str | None,
    odoo_bin: str,
    output_format: str,
    dry_run: bool,
) -> None:
    """Verify one native task checkout without mutating any system."""
    mode = _mode(output_format)
    client = _client_or_failure(profile, workspace_id, odoo_bin, mode, "context")
    if client is not None:
        _run_context(
            client,
            _request(checkout_path, project, multica_project, issue, run_id, repository_url),
            mode,
            dry_run,
        )


@cli.group("env")
def env() -> None:
    """Odoo environment preparation operations."""


def _git_resource(client: MulticaOdooClient, project: Path) -> object:
    """Resolve the core Git resource without adding a second core adapter."""
    candidates = (project / "odoo.conf", project / ".odoo.conf", project / ".odcli" / "odoo.conf")
    for candidate in candidates:
        if candidate.is_file():
            return client.core.instance.from_config(candidate).git
    raise click.UsageError("project has no readable odoo.conf for the core Git resource")


@cli.group(
    "git",
    invoke_without_command=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
@click.argument("checkout_path", type=click.Path(path_type=Path), required=False)
@click.option("--project", "project", required=False, type=click.Path(path_type=Path))
@click.option("--multica-project", required=False)
@click.option("--issue", required=False)
@click.option("--run", "run_id", required=False)
@click.option("--repository-url", required=False)
@click.option("--profile", default=None)
@click.option("--workspace-id", default=None)
@click.option("--odoo-bin", default=lambda: os.environ.get("ODCLI_ODOO_BIN", "odoo"))
@click.option(
    "--format", "output_format", type=click.Choice(["rich", "json", "toon"]), default="rich"
)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def git(
    ctx: click.Context,
    checkout_path: Path | None,
    project: Path | None,
    multica_project: str | None,
    issue: str | None,
    run_id: str | None,
    repository_url: str | None,
    profile: str | None,
    workspace_id: str | None,
    odoo_bin: str,
    output_format: str,
    dry_run: bool,
) -> None:
    """Run named sync or pass native Git after a literal ``--``."""
    if ctx.invoked_subcommand is not None:
        return
    if "--" not in tuple(ctx.args):
        raise click.UsageError("raw Git arguments require a literal `--` delimiter")
    if (
        checkout_path is None
        or project is None
        or not all(value is not None for value in (multica_project, issue, run_id, repository_url))
    ):
        raise click.UsageError(
            "raw Git requires CHECKOUT_PATH and complete Multica context options"
        )
    args = tuple(ctx.args)
    delimiter = args.index("--")
    native_args = args[delimiter + 1 :]
    client = _client_or_failure(profile, workspace_id, odoo_bin, _mode(output_format), "git")
    if client is None:
        return
    try:
        context = client.context(
            _request(
                checkout_path,
                project,
                multica_project or "",
                issue or "",
                run_id or "",
                repository_url or "",
            )
        )
        resource = _git_resource(client, project)
        command = client.git_command(
            context,
            native_args,
            git_resource=resource,
            project_root=project,
        )
        if dry_run:
            _finish(
                _mode(output_format),
                success_document(command="git", result=model_to_dict(command.plan), dry_run=True),
            )
            return
        result = command.run()
        raise click.exceptions.Exit(result.returncode)  # noqa: TRY301
    except click.exceptions.Exit:
        raise
    except BaseException as error:
        _failure(_mode(output_format), "git", error)


@git.command("sync")
@click.option("--base", default=None)
@click.option("--push", is_flag=True)
@click.option("--project", "project", required=True, type=click.Path(path_type=Path))
@click.option("--multica-project", required=True)
@click.option("--issue", required=True)
@click.option("--run", "run_id", required=True)
@click.option("--repository-url", required=True)
@click.argument("checkout_path", type=click.Path(path_type=Path))
@click.option("--profile", default=None)
@click.option("--workspace-id", default=None)
@click.option("--odoo-bin", default=lambda: os.environ.get("ODCLI_ODOO_BIN", "odoo"))
@click.option(
    "--format", "output_format", type=click.Choice(["rich", "json", "toon"]), default="rich"
)
@click.option("--dry-run", is_flag=True)
def git_sync(
    base: str | None,
    push: bool,
    project: Path,
    multica_project: str,
    issue: str,
    run_id: str,
    repository_url: str,
    checkout_path: Path,
    profile: str | None,
    workspace_id: str | None,
    odoo_bin: str,
    output_format: str,
    dry_run: bool,
) -> None:
    mode = _mode(output_format)
    client = _client_or_failure(profile, workspace_id, odoo_bin, mode, "git.sync")
    if client is None:
        return
    try:
        context = client.context(
            _request(checkout_path, project, multica_project, issue, run_id, repository_url)
        )
        command = client.sync_command(
            context,
            git_resource=_git_resource(client, project),
            project_root=project,
            base=base,
            push=push,
        )
        if dry_run:
            _finish(
                mode,
                success_document(
                    command="git.sync", result=model_to_dict(command.plan), dry_run=True
                ),
            )
            return
        value = command.run()
        _finish(mode, success_document(command="git.sync", result=model_to_dict(value)))
    except click.exceptions.Exit:
        raise
    except BaseException as error:
        _failure(mode, "git.sync", error)


@cli.group("gitlab")
def gitlab() -> None:
    """GitLab-specific extension operations."""


@gitlab.group("mr")
def merge_request() -> None:
    """Merge-request operations."""


@merge_request.command("publish")
@click.argument("checkout_path", type=click.Path(path_type=Path))
@click.option("--project", "project", required=True, type=click.Path(path_type=Path))
@click.option("--multica-project", required=True)
@click.option("--issue", required=True)
@click.option("--run", "run_id", required=True)
@click.option("--repository-url", required=True)
@click.option("--source", "source_branch", required=True)
@click.option("--target", "target_branch", required=True)
@click.option("--title", required=True)
@click.option("--description-file", required=True, type=click.Path(path_type=Path))
@click.option("--assignee", default=None)
@click.option("--project-path", default=None)
@click.option("--profile", default=None)
@click.option("--workspace-id", default=None)
@click.option("--odoo-bin", default=lambda: os.environ.get("ODCLI_ODOO_BIN", "odoo"))
@click.option(
    "--format", "output_format", type=click.Choice(["rich", "json", "toon"]), default="rich"
)
@click.option("--dry-run", is_flag=True)
def merge_request_publish(
    checkout_path: Path,
    project: Path,
    multica_project: str,
    issue: str,
    run_id: str,
    repository_url: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description_file: Path,
    assignee: str | None,
    project_path: str | None,
    profile: str | None,
    workspace_id: str | None,
    odoo_bin: str,
    output_format: str,
    dry_run: bool,
) -> None:
    mode = _mode(output_format)
    client = _client_or_failure(profile, workspace_id, odoo_bin, mode, "gitlab.mr.publish")
    if client is None:
        return
    try:
        context = client.context(
            _request(checkout_path, project, multica_project, issue, run_id, repository_url)
        )
        command = client.publish_merge_request_command(
            context,
            project_root=project,
            git_resource=_git_resource(client, project),
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description_file=description_file,
            assignee=assignee,
            project_path=project_path,
        )
        if dry_run:
            _finish(
                mode,
                success_document(
                    command="gitlab.mr.publish", result=model_to_dict(command.plan), dry_run=True
                ),
            )
            return
        value = command.run()
        _finish(mode, success_document(command="gitlab.mr.publish", result=model_to_dict(value)))
    except click.exceptions.Exit:
        raise
    except BaseException as error:
        _failure(mode, "gitlab.mr.publish", error)


@env.command("prepare")
@click.argument("checkout_path", type=click.Path(path_type=Path))
@click.option("--project", "project", required=True, type=click.Path(path_type=Path))
@click.option("--multica-project", required=True)
@click.option("--issue", required=True)
@click.option("--run", "run_id", required=True)
@click.option("--repository-url", required=True)
@click.option("--base", "base_ref", required=True)
@click.option("--remote", "remote_name", default=None)
@click.option("--backup-id", default=None)
@click.option("--source-db", "source_database", default=None)
@click.option("--profile", default=None)
@click.option("--workspace-id", default=None)
@click.option("--odoo-bin", default=lambda: os.environ.get("ODCLI_ODOO_BIN", "odoo"))
@click.option(
    "--format", "output_format", type=click.Choice(["rich", "json", "toon"]), default="rich"
)
@click.option("--dry-run", is_flag=True)
def prepare_command(
    checkout_path: Path,
    project: Path,
    multica_project: str,
    issue: str,
    run_id: str,
    repository_url: str,
    base_ref: str,
    remote_name: str | None,
    backup_id: str | None,
    source_database: str | None,
    profile: str | None,
    workspace_id: str | None,
    odoo_bin: str,
    output_format: str,
    dry_run: bool,
) -> None:
    """Verify context, then prepare one isolated COPY environment."""
    request = _request(checkout_path, project, multica_project, issue, run_id, repository_url)
    mode = _mode(output_format)
    client = _client_or_failure(profile, workspace_id, odoo_bin, mode, "env.prepare")
    if client is not None:
        _run_prepare(
            client,
            PreparationRequest(
                context=request,
                base_ref=base_ref,
                remote_name=remote_name,
                backup_id=backup_id,
                source_database=source_database,
            ),
            mode,
            dry_run,
        )


__all__ = ["cli"]
