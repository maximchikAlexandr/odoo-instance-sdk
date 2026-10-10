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
    run_or_preview,
    success_document,
)
from odoo_instance_sdk.execution import ActionStep, Command, ExecutionPlan, JsonValue
from odoo_instance_sdk.internal.alternate_tool import (
    SHA_RE,
    AlternateIdentity,
    AlternateManifest,
    AlternateToolError,
    identity,
    install_alternate_tool,
    list_alternate_tools,
    remove_alternate_tool,
    selected_identity,
)
from odoo_instance_sdk.internal.proc import PreparedAction, RunContext


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


def _validate_slot_sha(sha: str) -> None:
    if not SHA_RE.fullmatch(sha):
        raise ValueError("revision must be a complete lowercase SHA")


def _slot_plan(command: Command[object]) -> JsonObject:
    step = command.plan.steps[0]
    if not isinstance(step, ActionStep) or not isinstance(step.details, dict):
        raise AlternateToolError("slot command has no resolved operation plan")
    return step.details


def _rich_install(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    if document.dry_run:
        return f"Plan: install numbered-{result.get('identifier', '')}."
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
    if document.dry_run:
        return f"Plan: remove numbered-{result.get('identifier', '')}."
    return f"Removed {result.get('kind', 'numbered')}-{result.get('identifier', '')}."


def slot_install_command(
    number: str, sha: str, *, replace: bool = False
) -> Command[AlternateManifest]:
    """Capture one immutable install snapshot for preview and execution."""
    _validate_slot_sha(sha)
    _canonical_only()
    identity_value = _slot_identity(number)
    details: dict[str, str | bool] = {
        "kind": identity_value.kind,
        "identifier": identity_value.identifier,
        "sha": sha,
        "replace": replace,
        "launcher": str(identity_value.launcher),
        "state": str(identity_value.state_root),
        "manager": str(identity_value.manager_root),
        "tool": str(identity_value.tool_root),
    }
    action = PreparedAction(
        step_id="slot-install",
        action="slot-install",
        description=f"Install numbered-{number} at the requested revision",
        details=details,
        mutating=True,
    )

    def install() -> AlternateManifest:
        return install_alternate_tool(identity_value, sha, replace=replace)

    def callback(context: RunContext[AlternateManifest]) -> AlternateManifest:
        context.action("slot-install")
        result = install()
        context.complete_action("slot-install")
        return result

    return Command.create(
        ExecutionPlan(
            steps=(
                ActionStep(
                    step_id="slot-install",
                    action="slot-install",
                    description=f"Install numbered-{number} at the requested revision",
                    details=cast("JsonValue", details),
                    mutating=True,
                ),
            )
        ),
        callback,
        (action,),
    )


def slot_remove_command(number: str) -> Command[JsonObject]:
    """Capture one immutable removal snapshot for preview and execution."""
    _canonical_only()
    identity_value = _slot_identity(number)
    details: dict[str, str | bool] = {
        "kind": identity_value.kind,
        "identifier": identity_value.identifier,
        "removed": True,
        "launcher": str(identity_value.launcher),
        "state": str(identity_value.state_root),
        "manager": str(identity_value.manager_root),
        "tool": str(identity_value.tool_root),
    }

    def remove() -> JsonObject:
        remove_alternate_tool(identity_value)
        return {
            "removed": True,
            "kind": identity_value.kind,
            "identifier": identity_value.identifier,
        }

    action = PreparedAction(
        step_id="slot-remove",
        action="slot-remove",
        description=f"Remove numbered-{number} after idle identity checks",
        details=details,
        mutating=True,
    )

    def callback(context: RunContext[JsonObject]) -> JsonObject:
        context.action("slot-remove")
        result = remove()
        context.complete_action("slot-remove")
        return result

    return Command.create(
        ExecutionPlan(
            steps=(
                ActionStep(
                    step_id="slot-remove",
                    action="slot-remove",
                    description=f"Remove numbered-{number} after idle identity checks",
                    details=cast("JsonValue", details),
                    mutating=True,
                ),
            )
        ),
        callback,
        (action,),
    )


@click.group(
    "slot",
    help=(
        "Manage canonical exact-revision numbered OdCLI slots. Numbered slots "
        "isolate only OdCLI-managed user state (SQLite catalogue, configuration, "
        "backups, environment artifacts, project runtime state, locks, bug reports, "
        "and migration/update journals). They do not isolate project checkouts, Odoo "
        "processes, PostgreSQL clusters or databases, filestores, Docker resources, "
        "or ports; use separate project copies or disjoint external-resource "
        "configuration before concurrent use."
    ),
)
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
@click.option("--dry-run", is_flag=True, default=False, help="Inspect without installing.")
@output_options
def slot_install(
    number: str,
    sha: str,
    replace: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        run_or_preview(
            lambda: slot_install_command(number, sha, replace=replace),
            command_name="slot.install",
            mode=mode,
            dry_run=dry_run,
            result=lambda manifest: cast(
                "JsonObject", cast("AlternateManifest", manifest).to_json()
            ),
            preview=lambda command: _slot_plan(cast("Command[object]", command)),
            rich=_rich_install,
        )
    except Exception as exc:
        fail(mode, "slot.install", exc, dry_run=dry_run)


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
@click.option("--dry-run", is_flag=True, default=False, help="Inspect without removing.")
@output_options
def slot_remove(number: str, dry_run: bool, output_format: str | None, json_output: bool) -> None:
    mode = resolve_output_mode(output_format, json_output)
    try:
        run_or_preview(
            lambda: slot_remove_command(number),
            command_name="slot.remove",
            mode=mode,
            dry_run=dry_run,
            result=lambda result: cast("JsonObject", result),
            preview=lambda command: _slot_plan(cast("Command[object]", command)),
            rich=_rich_remove,
        )
    except Exception as exc:
        fail(mode, "slot.remove", exc, dry_run=dry_run)


__all__ = [
    "slot_group",
    "slot_install",
    "slot_install_command",
    "slot_list",
    "slot_remove",
    "slot_remove_command",
]
