"""CLI adapter for addon scaffolding."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

import msgspec

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.commands.output import (
    command_options,
    fail,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import ScaffoldError
from odoo_instance_sdk.execution import JsonValue
from odoo_instance_sdk.internal.context import resolve_project
from odoo_instance_sdk.models.scaffold import ManifestValue, ScaffoldResult
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.scaffold_config import parse_manifest_override


def register_scaffold_command(cli: click.Group) -> None:
    """Register ``odcli scaffold``."""

    @cli.command("scaffold", help="Create an Odoo addon with the native scaffold renderer.")
    @click.argument("module_name")
    @click.argument("destination", required=False, type=click.Path())
    @click.option("--with", "include", multiple=True, help="Enable one optional component.")
    @click.option("--without", "exclude", multiple=True, help="Disable one optional component.")
    @click.option("--manifest", "manifest", multiple=True, help="Override one manifest KEY=VALUE.")
    @click.option("--project", type=click.Path(), default=None, help="Project path.")
    @command_options
    def scaffold(
        module_name: str,
        destination: str | None,
        include: tuple[str, ...],
        exclude: tuple[str, ...],
        manifest: tuple[str, ...],
        project: str | None,
        dry_run: bool,
        output_format: str | None,
        json_output: bool,
    ) -> None:
        mode = resolve_output_mode(output_format, json_output)
        try:
            loaded = _project(Path(project) if project is not None else None)
            overrides = _overrides(manifest)
            client = OdooClient(config=OdooClientConfig(executable="odoo"))
            target = Path(destination) if destination is not None else Path.cwd()
            status, _result = run_or_preview(
                lambda: client.scaffolds.create_command(
                    project=loaded,
                    module_name=module_name,
                    destination=target,
                    include=include,
                    exclude=exclude,
                    manifest=overrides,
                ),
                command_name="scaffold",
                mode=mode,
                dry_run=dry_run,
                result=_payload,
            )
        except SystemExit:
            raise
        except Exception as error:
            fail(mode, "scaffold", error, dry_run=dry_run)
        raise click.exceptions.Exit(status)


def _project(explicit: Path | None) -> ProjectConfig:
    resolved = resolve_project(explicit, cwd=Path.cwd())
    if isinstance(resolved, ProjectConfig):
        return resolved
    return ProjectConfig.load(resolved)


def _overrides(items: tuple[str, ...]) -> dict[str, ManifestValue]:
    parsed: dict[str, ManifestValue] = {}
    for item in items:
        key, value = parse_manifest_override(item)
        if key in parsed:
            raise ScaffoldError(
                f"duplicate manifest override: {key}",
                code="scaffold_invalid_manifest",
            )
        parsed[key] = value
    return parsed


def _payload(value: ScaffoldResult | None) -> dict[str, JsonValue]:
    if value is None:
        return {}
    return cast("dict[str, JsonValue]", msgspec.to_builtins(value))


__all__ = ["register_scaffold_command"]
