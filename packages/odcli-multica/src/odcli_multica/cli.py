"""The bounded ``odcli-multica`` command surface."""

from __future__ import annotations

import os
from pathlib import Path

import click
from multica_py import ClientConfig, MulticaClient

from odcli_multica.client import MulticaOdooClient, PrepareCommand
from odcli_multica.models import ContextRequest, PreparationRequest
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


def _request(
    checkout_path: Path,
    project: Path,
    multica_project: str,
    issue: str,
    run: str,
) -> ContextRequest:
    return ContextRequest(
        checkout_path=checkout_path,
        core_project=project,
        multica_project=multica_project,
        issue=issue,
        run=run,
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
    profile: str | None,
    workspace_id: str | None,
    odoo_bin: str,
    output_format: str,
    dry_run: bool,
) -> None:
    """Verify one native task checkout without mutating any system."""
    _run_context(
        _client(profile, workspace_id, odoo_bin),
        _request(checkout_path, project, multica_project, issue, run_id),
        _mode(output_format),
        dry_run,
    )


@cli.group("env")
def env() -> None:
    """Odoo environment preparation operations."""


@env.command("prepare")
@click.argument("checkout_path", type=click.Path(path_type=Path))
@click.option("--project", "project", required=True, type=click.Path(path_type=Path))
@click.option("--multica-project", required=True)
@click.option("--issue", required=True)
@click.option("--run", "run_id", required=True)
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
    request = _request(checkout_path, project, multica_project, issue, run_id)
    _run_prepare(
        _client(profile, workspace_id, odoo_bin),
        PreparationRequest(
            context=request,
            base_ref=base_ref,
            remote_name=remote_name,
            backup_id=backup_id,
            source_database=source_database,
        ),
        _mode(output_format),
        dry_run,
    )


__all__ = ["cli"]
