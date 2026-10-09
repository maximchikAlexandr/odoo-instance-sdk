"""Standalone publication Click leaves.

The central CLI registers these only during terminal convergence.  Keeping the
leaves importable here lets the SDK and the extension test/help them in
isolation without introducing a second registry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import rich_click as click
from msgspec import to_builtins

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
        raise click.UsageError("publication commands require an OdooClient context")
    return cast("OdooClient", client)


@click.command(name="publish")
@click.option("--project", type=click.Path(path_type=Path), default=None)
@click.option("--env", "environment", default=None)
@click.option("--dry-run", is_flag=True, help="Show the captured publication plan only.")
@click.option("--settings", type=click.Path(path_type=Path), default=None, hidden=True)
@click.pass_context
def publish_cli(
    ctx: click.Context,
    project: Path | None,
    environment: str | None,
    dry_run: bool,
    settings: Path | None,
) -> None:
    """Publish exactly one project or environment runtime through Caddy."""
    selected = _selector(str(project) if project is not None else None, environment)
    loaded = PublicationSettings.load(settings)
    command = _client(ctx).publication.publish_command(selected, settings=loaded)
    if dry_run:
        click.echo(json.dumps(to_builtins(command.plan), sort_keys=True))
        return
    click.echo(json.dumps(to_builtins(command.run()), sort_keys=True))


@click.command(name="unpublish")
@click.option("--project", type=click.Path(path_type=Path), default=None)
@click.option("--env", "environment", default=None)
@click.option("--dry-run", is_flag=True, help="Show the captured publication plan only.")
@click.option("--settings", type=click.Path(path_type=Path), default=None, hidden=True)
@click.pass_context
def unpublish_cli(
    ctx: click.Context,
    project: Path | None,
    environment: str | None,
    dry_run: bool,
    settings: Path | None,
) -> None:
    """Remove exactly one project or environment route from Caddy."""
    selected = _selector(str(project) if project is not None else None, environment)
    loaded = PublicationSettings.load(settings)
    if environment is not None:
        command = _client(ctx).publication.unpublish_command(
            ("environment", environment), settings=loaded
        )
    else:
        command = _client(ctx).publication.unpublish_command(selected, settings=loaded)
    if dry_run:
        click.echo(json.dumps(to_builtins(command.plan), sort_keys=True))
        return
    click.echo(json.dumps(to_builtins(command.run()), sort_keys=True))


publish = publish_cli
unpublish = unpublish_cli

__all__ = ["publish", "publish_cli", "unpublish", "unpublish_cli"]
