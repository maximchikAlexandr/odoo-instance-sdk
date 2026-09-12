"""CLI registration for translation export."""

from __future__ import annotations

import sys
from collections.abc import Callable
from io import StringIO
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.execution import Command
else:
    import rich_click as click
from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    fail,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.internal.automation import (
    TranslationExportResult,
)
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.internal.cli_format import rich_cell


def _rich_translation_export(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    exports = result.get("exports", [])
    table = Table("Module", "Language", "File", "Size", title="Translation export")
    if isinstance(exports, list) and exports:
        for item in exports:
            if not isinstance(item, dict):
                continue
            size = item.get("bytes_written")
            table.add_row(
                rich_cell(item.get("module", "")),
                rich_cell(item.get("requested_lang", "")),
                rich_cell(item.get("actual_filename", "")),
                rich_cell(
                    _human_bytes(size)
                    if isinstance(size, int) and not isinstance(size, bool)
                    else "—"
                ),
            )
    else:
        table.add_row("(none)", "—", "—", "—")
    output = StringIO()
    console = Console(file=output, color_system=None, width=180)
    console.print(table)
    return output.getvalue().rstrip()


@click.group("translations", help="Export Odoo module translations.")
def _translations_group() -> None:
    pass


@_translations_group.command("export", help="Export selected module translations.")
@click.option("--module", "modules", multiple=True, required=True, help="Module name.")
@click.option("--language", "languages", multiple=True, required=True, help="Language code.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def translations_export(
    ctx: CliContext,
    modules: tuple[str, ...],
    languages: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = cli_context.ready_instance(ctx)
        status, _results = run_or_preview(
            lambda: cast(
                "Callable[..., Command[list[TranslationExportResult]]]",
                getattr(sys.modules["odoo_instance_sdk.cli"], "export_translations_command"),
            )(
                runtime_context.instance,
                tuple(modules),
                tuple(languages),
                worktree_root=runtime_context.worktree_path(),
            ),
            command_name="translations.export",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: {
                "exports": [
                    {
                        "module": item.module,
                        "requested_lang": item.requested_lang,
                        "actual_filename": item.actual_filename,
                        "path": str(item.path),
                        "bytes_written": item.bytes_written,
                    }
                    for item in cast("list[TranslationExportResult]", value or [])
                ]
            },
            rich=_rich_translation_export,
            progress=True,
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "translations.export", e, dry_run=dry_run)
    raise click.exceptions.Exit(status)


def register_translation_commands(group: click.Group) -> None:
    """Attach translation commands through the shared CLI seam."""
    group.add_command(_translations_group)


__all__ = ["register_translation_commands"]
