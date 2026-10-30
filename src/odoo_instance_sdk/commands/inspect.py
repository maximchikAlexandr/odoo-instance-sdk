"""Bounded SDK-first inspection commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click
    import msgspec
else:
    import rich_click as click

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    OutputMode,
    bordered_table,
    emit,
    fail,
    model_to_dict,
    output_options,
    render_rich_text,
    resolve_output_mode,
    success_document,
)
from odoo_instance_sdk.internal.cli_format import rich_cell


def _rich_inspection(document: OutputDocument, *, title: str) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    table = bordered_table("Field", "Value", title=title)
    for key, value in result.items():
        table.add_row(key.replace("_", " ").title(), rich_cell(value if value is not None else "—"))
    return render_rich_text(table)


def _emit_inspection(operation: str, result: msgspec.Struct, mode: OutputMode) -> None:
    try:
        emit(
            success_document(
                command=f"inspect {operation}", result=model_to_dict(result), dry_run=False
            ),
            mode,
            rich=lambda document: _rich_inspection(document, title=f"Odoo {operation}"),
        )
    except Exception as exc:
        fail(mode, f"inspect {operation}", str(exc), dry_run=False)


@click.group(
    "inspect",
    help=(
        "Read bounded registry metadata through the selected project or environment. "
        "Valid missing objects return exists=false; reads roll back and require no HTTP server.\n\n"
        "Examples:\n  odcli inspect model res.partner\n  odcli inspect field res.partner email\n"
        "  odcli inspect ref base.user_admin"
    ),
)
def inspect_group() -> None:
    """Inspect a small set of read-only Odoo registry facts."""


@inspect_group.command("model", help="Inspect bounded metadata for one registry model.")
@click.argument("name")
@output_options
@pass_cli_context
def inspect_model(ctx: CliContext, name: str, output_format: str | None, json_output: bool) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        instance = cli_context.ready_instance(ctx).instance
        _emit_inspection("model", instance.inspector.model_command(name).run(), mode)
    except Exception as exc:
        fail(mode, "inspect model", str(exc), dry_run=False)


@inspect_group.command("field", help="Inspect bounded metadata for one model field.")
@click.argument("model")
@click.argument("name")
@output_options
@pass_cli_context
def inspect_field(
    ctx: CliContext,
    model: str,
    name: str,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        instance = cli_context.ready_instance(ctx).instance
        _emit_inspection("field", instance.inspector.field_command(model, name).run(), mode)
    except Exception as exc:
        fail(mode, "inspect field", str(exc), dry_run=False)


@inspect_group.command("ref", help="Resolve one XML ID without exposing record fields.")
@click.argument("xmlid")
@output_options
@pass_cli_context
def inspect_ref(ctx: CliContext, xmlid: str, output_format: str | None, json_output: bool) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        instance = cli_context.ready_instance(ctx).instance
        _emit_inspection("ref", instance.inspector.ref_command(xmlid).run(), mode)
    except Exception as exc:
        fail(mode, "inspect ref", str(exc), dry_run=False)


__all__ = ["inspect_group"]
