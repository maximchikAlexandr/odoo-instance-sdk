"""Command line for odcli-codex."""

from __future__ import annotations

from pathlib import Path

import click

from odcli_codex.client import CodexOdooClient
from odcli_codex.errors import CodexWorkflowError
from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.commands.context import CliContext, resolve_project_path
from odoo_instance_sdk.commands.output import (
    OutputMode,
    emit,
    failure_document,
    model_to_dict,
    output_options,
    resolve_output_mode,
    success_document,
)
from odoo_instance_sdk.commands.plain_line import emit_plain_line
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.resources.git_runtime import repository_resource


@click.group()
def cli() -> None:
    """Codex workflows for Odoo translations and commit descriptions."""


@cli.group("translations")
def translations() -> None:
    """Fill missing entries in an existing Odoo catalog."""


@translations.command("fill")
@click.argument("catalog", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--context", "contexts", multiple=True, help="Tracked project file or directory.")
@click.option("--model", default=None, help="Codex model. Overrides [codex].model.")
@click.option(
    "--language", default=None, help="Language used when the catalog does not resolve one."
)
@click.option("--project", "project", type=click.Path(path_type=Path), default=None)
@click.option("--dry-run", is_flag=True, help="Show the plan without calling Codex or writing.")
@output_options
def translations_fill(
    catalog: Path,
    contexts: tuple[str, ...],
    model: str | None,
    language: str | None,
    project: Path | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Fill empty msgstr values in one .po file."""
    mode = resolve_output_mode(output_format, json_output)
    try:
        root = resolve_project_path(CliContext(project=str(project) if project else None))
        command = _client().translations.fill_command(
            catalog,
            project_root=root,
            contexts=contexts,
            model=model,
            language=language,
        )
        if dry_run:
            _finish(
                mode,
                "translations.fill",
                model_to_dict(command.plan),
                dry_run=True,
            )
            return
        _finish(mode, "translations.fill", model_to_dict(command.run()), dry_run=False)
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        _fail(mode, "translations.fill", exc, dry_run)


@cli.group("git")
def git() -> None:
    """Generate an English commit description."""


@git.command("message")
@click.option("--context", "contexts", multiple=True, help="Tracked project file or directory.")
@click.option("--model", default=None, help="Codex model. Overrides [codex].model.")
@click.option("--project", "project", type=click.Path(path_type=Path), default=None)
@click.option("--plain", is_flag=True, help="Write only the description line to stdout.")
@click.option("--dry-run", is_flag=True, help="Show the plan without calling Codex.")
@output_options
def git_message(
    contexts: tuple[str, ...],
    model: str | None,
    project: Path | None,
    plain: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Print one English description. Does not stage or commit."""
    if plain and (output_format is not None or json_output or dry_run):
        raise click.UsageError("--plain cannot be combined with --format or --dry-run")
    mode = resolve_output_mode(output_format, json_output)
    try:
        root = resolve_project_path(CliContext(project=str(project) if project else None))
        command = _client().git.message_command(
            repository_resource(root),
            project_root=root,
            contexts=contexts,
            model=model,
        )
        if dry_run:
            _finish(mode, "git.message", model_to_dict(command.plan), dry_run=True)
            return
        description = command.run()
        if plain:
            emit_plain_line(description.description)
            return
        _finish(mode, "git.message", model_to_dict(description), dry_run=False)
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        _fail(mode, "git.message", exc, dry_run)


def _client() -> CodexOdooClient:
    return CodexOdooClient(OdooClient(config=OdooClientConfig(executable="odoo")))


def _finish(
    mode: OutputMode,
    command: str,
    result: dict[str, JsonValue],
    *,
    dry_run: bool,
) -> None:
    status = emit(
        success_document(command=command, result=result, dry_run=dry_run),
        mode,
    )
    if status:
        raise click.exceptions.Exit(status)


def _fail(mode: OutputMode, command: str, exc: BaseException, dry_run: bool) -> None:
    status = emit(
        failure_document(
            command=command,
            dry_run=dry_run,
            error_code="codex_workflow" if isinstance(exc, CodexWorkflowError) else None,
            error_message=exc,
        ),
        mode,
    )
    raise click.exceptions.Exit(status or 1)
