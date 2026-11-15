"""Console script for binding an OpenSpec change to the current environment."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import NoReturn, TypeVar, cast

import click
import msgspec

from odcli_openspec.client import OpenSpecOdooClient
from odcli_openspec.errors import OpenSpecError
from odoo_instance_sdk import OdooClient, OdooClientConfig
from odoo_instance_sdk.execution import Command, JsonValue, canonical_plan_projection

T = TypeVar("T")


@click.group()
def cli() -> None:
    """Bind OpenSpec changes to OdCLI environments."""


@cli.group("env")
def env_group() -> None:
    """Bind or unbind the environment that contains the current directory."""


@env_group.command("bind")
@click.argument("change")
@click.option("--dry-run", is_flag=True, default=False, help="Show the plan without running it.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
def bind_command(change: str, dry_run: bool, output_format: str) -> None:
    """Validate CHANGE in the current environment and store the binding."""
    _run(lambda: _client().environments.bind_command(change), dry_run, output_format)


@env_group.command("unbind")
@click.option("--dry-run", is_flag=True, default=False, help="Show the plan without running it.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
def unbind_command(dry_run: bool, output_format: str) -> None:
    """Remove only this extension's binding for the current environment."""
    _run(_client().environments.unbind_command, dry_run, output_format)


def main() -> None:
    """Run the console script and map usage errors to Click exit codes."""
    try:
        cli(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc


def _client() -> OpenSpecOdooClient:
    return OpenSpecOdooClient(OdooClient(config=OdooClientConfig(executable="odoo")))


def _run(build: Callable[[], Command[T]], dry_run: bool, output_format: str) -> None:
    try:
        command = build()
    except OpenSpecError as exc:
        _fail(exc)
    if dry_run:
        _emit(canonical_plan_projection(command.plan), output_format)
        return
    try:
        _emit(cast("JsonValue", msgspec.to_builtins(command.run())), output_format)
    except OpenSpecError as exc:
        _fail(exc)


def _fail(exc: OpenSpecError) -> NoReturn:
    click.echo(f"{exc.reason}: {exc}", err=True)
    raise SystemExit(1)


def _emit(payload: JsonValue, output_format: str) -> None:
    if output_format == "json":
        click.echo(json.dumps(payload, sort_keys=True))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            click.echo(f"{key}: {value}")
        return
    click.echo(str(payload))


if __name__ == "__main__":
    main()
