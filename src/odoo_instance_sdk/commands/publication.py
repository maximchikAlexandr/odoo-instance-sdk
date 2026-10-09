"""Standalone publication Click leaves.

The central CLI registers these only during terminal convergence.  Keeping the
leaves importable here lets the SDK and the extension test/help them in
isolation without introducing a second registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from msgspec import to_builtins

from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputMode,
    emit,
    failure_document,
    success_document,
)
from odoo_instance_sdk.resources.publication import PublicationSettings

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


def _emit_result(command_name: str, value: object, *, dry_run: bool, output_format: str) -> None:
    payload = to_builtins(value)
    if not isinstance(payload, dict):
        raise click.ClickException("publication result is not an object")
    emit(
        success_document(
            command=command_name,
            result=cast("JsonObject", payload),
            dry_run=dry_run,
        ),
        OutputMode(output_format),
    )


def _emit_failure(
    command_name: str, error: Exception, *, dry_run: bool, output_format: str
) -> None:
    emit(
        failure_document(
            command=command_name,
            dry_run=dry_run,
            error_code="publication_failed",
            error_message=str(error),
        ),
        OutputMode(output_format),
    )


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
    try:
        loaded = PublicationSettings.load(settings)
        if environment is not None:
            selected_environment = _client(ctx).environments.get(environment)
            command = _client(ctx).publication.publish_command(
                selected_environment, settings=loaded
            )
        else:
            selected = _selector(str(project) if project is not None else None, None)
            command = _client(ctx).publication.publish_command(selected, settings=loaded)
    except click.UsageError:
        raise
    except Exception as error:
        _emit_failure("publish", error, dry_run=dry_run, output_format=output_format)
        raise click.exceptions.Exit(1) from error
    try:
        if dry_run:
            _emit_result("publish", command.plan, dry_run=True, output_format=output_format)
            return
        _emit_result("publish", command.run(), dry_run=False, output_format=output_format)
    except click.UsageError:
        raise
    except Exception as error:
        _emit_failure("publish", error, dry_run=dry_run, output_format=output_format)
        raise click.exceptions.Exit(1) from error


@click.command(name="unpublish")
@click.option("--project", type=click.Path(path_type=Path), default=None)
@click.option("--env", "environment", default=None)
@click.option("--dry-run", is_flag=True, help="Show the captured publication plan only.")
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
    output_format: str,
    settings: Path | None,
) -> None:
    """Remove exactly one project or environment route from Caddy."""
    try:
        selected = _selector(str(project) if project is not None else None, environment)
        loaded = PublicationSettings.load(settings)
        if environment is not None:
            command = _client(ctx).publication.unpublish_command(
                ("environment", environment), settings=loaded
            )
        else:
            command = _client(ctx).publication.unpublish_command(selected, settings=loaded)
    except click.UsageError:
        raise
    except Exception as error:
        _emit_failure("unpublish", error, dry_run=dry_run, output_format=output_format)
        raise click.exceptions.Exit(1) from error
    try:
        if dry_run:
            _emit_result("unpublish", command.plan, dry_run=True, output_format=output_format)
            return
        _emit_result("unpublish", command.run(), dry_run=False, output_format=output_format)
    except Exception as error:
        _emit_failure("unpublish", error, dry_run=dry_run, output_format=output_format)
        raise click.exceptions.Exit(1) from error


publish = publish_cli
unpublish = unpublish_cli

__all__ = ["publish", "publish_cli", "unpublish", "unpublish_cli"]
