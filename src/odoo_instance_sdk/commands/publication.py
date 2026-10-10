"""Standalone publication Click leaves.

The central CLI registers these only during terminal convergence.  Keeping the
leaves importable here lets the SDK and the extension test/help them in
isolation without introducing a second registry.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from odoo_instance_sdk.commands.output import (
    OutputMode,
    fail,
    model_to_dict,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.resources.publication import PublicationResult, PublicationSettings

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


def _selector(project: str | None, environment: str | None) -> Path | str:
    if (project is None) == (environment is None):
        raise click.UsageError("pass exactly one of --project PATH or --env ENVIRONMENT")
    return Path(project) if project is not None else cast("str", environment)


def _client(ctx: click.Context) -> OdooClient:
    client = ctx.find_root().obj
    if client is None or not hasattr(client, "publication"):
        from odoo_instance_sdk.client import OdooClient
        from odoo_instance_sdk.config import OdooClientConfig

        client = OdooClient(config=OdooClientConfig(executable="odoo"))
    return cast("OdooClient", client)


def _run_publication(
    *,
    command_name: str,
    build_command: Callable[[], Command[PublicationResult]],
    dry_run: bool,
    output_format: str,
    confirm_required: bool = False,
    yes: bool = False,
) -> None:
    mode = resolve_output_mode(output_format, False)

    def confirm() -> None:
        if mode is OutputMode.RICH:
            click.confirm("Unpublish this owned route?", default=False, abort=True)
            return
        fail(
            mode,
            command_name,
            "unpublish requires --yes in machine output mode",
            dry_run=False,
            error_code="confirmation_required",
        )

    try:
        status, _ = run_or_preview(
            build_command,
            command_name=command_name,
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value is not None else {},
            confirm=confirm if confirm_required and not yes else None,
        )
    except click.exceptions.Exit:
        raise
    except Exception as error:
        fail(mode, command_name, error, dry_run=dry_run, error_code="publication_failed")
        return
    if status:
        raise click.exceptions.Exit(status)


def _build_publish_command(
    ctx: click.Context,
    project: Path | None,
    environment: str | None,
    settings: Path | None,
) -> Command[PublicationResult]:
    client = _client(ctx)
    selected = _selector(str(project) if project is not None else None, environment)
    loaded = PublicationSettings.load(settings)
    target = client.environments.get(selected) if isinstance(selected, str) else selected
    return client.publication.publish_command(target, settings=loaded)


def _build_unpublish_command(
    ctx: click.Context,
    project: Path | None,
    environment: str | None,
    settings: Path | None,
) -> Command[PublicationResult]:
    client = _client(ctx)
    selected = _selector(str(project) if project is not None else None, environment)
    loaded = PublicationSettings.load(settings)
    target = ("environment", selected) if isinstance(selected, str) else selected
    return client.publication.unpublish_command(target, settings=loaded)


@click.command(name="publish")
@click.option("--project", type=click.Path(path_type=Path), default=None)
@click.option("--env", "environment", default=None)
@click.option("--dry-run", is_flag=True, help="Show the captured publication plan only.")
@click.option(
    "--format", "output_format", type=click.Choice(["rich", "json", "toon"]), default="rich"
)
@click.option("--settings", type=click.Path(path_type=Path), default=None, hidden=True)
@click.pass_context
def publish_cli(
    ctx: click.Context,
    project: Path | None,
    environment: str | None,
    dry_run: bool,
    output_format: str,
    settings: Path | None,
) -> None:
    """Publish exactly one project or environment runtime through Caddy."""
    _run_publication(
        command_name="publish",
        build_command=lambda: _build_publish_command(ctx, project, environment, settings),
        dry_run=dry_run,
        output_format=output_format,
    )


@click.command(name="unpublish")
@click.option("--project", type=click.Path(path_type=Path), default=None)
@click.option("--env", "environment", default=None)
@click.option("--dry-run", is_flag=True, help="Show the captured publication plan only.")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@click.option(
    "--format", "output_format", type=click.Choice(["rich", "json", "toon"]), default="rich"
)
@click.option("--settings", type=click.Path(path_type=Path), default=None, hidden=True)
@click.pass_context
def unpublish_cli(
    ctx: click.Context,
    project: Path | None,
    environment: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str,
    settings: Path | None,
) -> None:
    """Remove exactly one project or environment route from Caddy."""
    _run_publication(
        command_name="unpublish",
        build_command=lambda: _build_unpublish_command(ctx, project, environment, settings),
        dry_run=dry_run,
        output_format=output_format,
        confirm_required=True,
        yes=yes,
    )


publish = publish_cli
unpublish = unpublish_cli

__all__ = ["publish", "publish_cli", "unpublish", "unpublish_cli"]
