"""Canonical numbered-slot policy and output adapters."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from click.types import IntParamType

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    bordered_table,
    emit,
    fail,
    output_options,
    render_rich_text,
    resolve_output_mode,
    success_document,
)
from odoo_instance_sdk.internal.alternate_tool import (
    AlternateIdentity,
    AlternateToolError,
    identity,
    install_alternate_tool,
    list_alternate_tools,
    remove_alternate_tool,
    selected_identity,
)


class _SlotNumberType(IntParamType):
    name = "number"

    def convert(  # type: ignore[override]
        self, value: str | int, param: click.Parameter | None, ctx: click.Context | None
    ) -> str:
        # IntParamType keeps Click's negative-number argument parsing while the
        # adapter retains the original spelling for strict identity validation.
        return str(value)


_SLOT_NUMBER = _SlotNumberType()


def _canonical_only() -> None:
    """Reject manager operations before inspecting any alternate state."""
    if selected_identity() is not None:
        raise AlternateToolError("slot management is available only through canonical odcli")


def _slot_identity(number: str) -> AlternateIdentity:
    return identity("numbered", number, home=Path.home())


def _rich_install(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    return f"Installed {result.get('kind', 'numbered')}-{result.get('identifier', '')}."


def _rich_list(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    slots = result.get("slots", [])
    if not isinstance(slots, list) or not slots:
        return "No numbered slots installed."
    table = bordered_table("Slot", "SHA", "Launcher", "State", "Health")
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        table.add_row(
            str(slot.get("identifier", "")),
            str(slot.get("sha", "—")),
            str(slot.get("launcher", "—")),
            str(slot.get("state", "—")),
            "healthy" if slot.get("healthy") is True else "unhealthy",
        )
    return render_rich_text(table)


def _rich_remove(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    return f"Removed {result.get('kind', 'numbered')}-{result.get('identifier', '')}."


@click.group("slot", help="Manage canonical exact-revision numbered OdCLI slots.")
def slot_group() -> None:
    """Install, inspect, and remove numbered OdCLI revisions."""


@slot_group.command(
    "install",
    context_settings={"ignore_unknown_options": True},
    help=(
        "Install NUMBER at an exact 40-character lowercase SHA. "
        "Use --replace only for an idle existing slot; slot state remains isolated."
    ),
)
@click.argument("number", type=_SLOT_NUMBER)
@click.argument("sha")
@click.option("--replace", is_flag=True, help="Replace an existing idle numbered slot.")
@output_options
def slot_install(
    number: str,
    sha: str,
    replace: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        _canonical_only()
        manifest = install_alternate_tool(_slot_identity(number), sha, replace=replace)
        result = cast("JsonObject", manifest.to_json())
        emit(success_document(command="slot.install", result=result), mode, rich=_rich_install)
    except Exception as exc:
        fail(mode, "slot.install", exc, dry_run=False)


@slot_group.command(
    "list",
    help="List bounded numbered-slot metadata without executing installed revisions.",
)
@output_options
def slot_list(output_format: str | None, json_output: bool) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        _canonical_only()
        slots = list_alternate_tools("numbered")
        result: JsonObject = {"slots": [cast("JsonObject", slot) for slot in slots]}
        emit(success_document(command="slot.list", result=result), mode, rich=_rich_list)
    except Exception as exc:
        fail(mode, "slot.list", exc, dry_run=False)


@slot_group.command(
    "remove",
    context_settings={"ignore_unknown_options": True},
    help=(
        "Remove NUMBER only after shared lifecycle identity and idle-lock checks; "
        "canonical and neighboring roots are preserved."
    ),
)
@click.argument("number", type=_SLOT_NUMBER)
@output_options
def slot_remove(number: str, output_format: str | None, json_output: bool) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        _canonical_only()
        identity_value = _slot_identity(number)
        remove_alternate_tool(identity_value)
        result: JsonObject = {
            "removed": True,
            "kind": identity_value.kind,
            "identifier": identity_value.identifier,
        }
        emit(success_document(command="slot.remove", result=result), mode, rich=_rich_remove)
    except Exception as exc:
        fail(mode, "slot.remove", exc, dry_run=False)


__all__ = ["slot_group", "slot_install", "slot_list", "slot_remove"]
