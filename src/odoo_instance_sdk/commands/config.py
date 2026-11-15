"""CLI adapter for the user configuration path."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import rich_click as click

from odoo_instance_sdk.commands.output import (
    emit,
    fail,
    output_options,
    resolve_output_mode,
    success_document,
)
from odoo_instance_sdk.scaffold_config import user_config_path


def register_config_commands(cli: click.Group) -> None:
    """Register ``odcli config path``."""

    @cli.group("config", help="Show OdCLI user configuration.")
    def config() -> None:
        """Show user configuration locations."""

    @config.command("path", help="Print the user configuration file path.")
    @output_options
    def path(output_format: str | None, json_output: bool) -> None:
        mode = resolve_output_mode(output_format, json_output)
        try:
            selected = user_config_path()
        except Exception as error:
            fail(mode, "config.path", error, dry_run=False)
        emit(
            success_document(command="config.path", result={"path": str(selected)}),
            mode,
            rich=lambda _document: str(selected),
        )


__all__ = ["register_config_commands"]
