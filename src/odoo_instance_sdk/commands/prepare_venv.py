from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.execution import JsonValue
else:
    import rich_click as click

from odoo_instance_sdk.commands.output import (
    command_options,
    fail,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.project_init import prepare_venv_command


def register_prepare_venv_command(cli: click.Group) -> None:
    """Register the SDK-first primary checkout venv operation."""

    @cli.command("prepare-venv", help="Create or refresh the primary project virtualenv.")
    @click.option("--python", required=True, help="Python executable or uv selector.")
    @click.option("--venv", required=True, type=click.Path(), help="Project virtualenv path.")
    @click.option("--project", type=click.Path(exists=False), default=None, help="Project path.")
    @command_options
    def prepare_venv(
        python: str,
        venv: str,
        project: str | None,
        dry_run: bool,
        output_format: str | None,
        json_output: bool,
    ) -> None:
        mode = resolve_output_mode(output_format, json_output)
        root = Path(project) if project is not None else Path.cwd()
        try:
            status, _ = run_or_preview(
                lambda: prepare_venv_command(root, python=python, venv=venv),
                command_name="prepare-venv",
                mode=mode,
                dry_run=dry_run,
                result=lambda value: cast("dict[str, JsonValue]", value or {}),
            )
        except SystemExit:
            raise
        except Exception as error:
            fail(mode, "prepare-venv", error, dry_run=dry_run)
        raise click.exceptions.Exit(status)


__all__ = ["register_prepare_venv_command"]
