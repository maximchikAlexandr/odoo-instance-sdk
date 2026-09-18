"""Shared CLI-only orchestration for safe variadic multi-target deletion.

No generic bulk SDK, parallel deletion, or orchestration hierarchy lives
here.  Each command reuses its existing single-target resolvers, validators,
and command builders; this module only sequences preflight, confirmation,
sequential execution with per-target revalidation, per-target results, and
non-zero exit on partial failure, and produces one ordered aggregate plan
for ``--dry-run``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    emit,
    emit_json_envelope,
    failure_document,
    rich_print,
    sanitize_terminal_text,
    success_document,
)

_PlanT = TypeVar("_PlanT")


class TargetError(Exception):
    """One sanitized planning-preflight abort for a single target."""

    def __init__(self, target: str, message: str) -> None:
        self.target = target
        self.message = message
        super().__init__(f"{target}: {message}")


def require_machine_confirmation(
    output_mode: OutputMode,
    yes: bool,
    *,
    command: str,
) -> None:
    if output_mode is OutputMode.RICH or yes:
        return
    emit_json_envelope(
        ok=False,
        command=command,
        error_code="confirmation_required",
        error_message=f"{command} requires --yes in machine output mode",
        mode=output_mode,
    )
    raise click.exceptions.Exit(1)


def run_multi_target_deletion(
    targets: Sequence[_PlanT],
    *,
    command: str,
    mode: OutputMode,
    dry_run: bool,
    yes: bool,
    target_id: Callable[[_PlanT], str],
    target_label: Callable[[_PlanT], str],
    build_plan: Callable[[_PlanT], JsonObject],
    execute_target: Callable[[_PlanT], tuple[bool, JsonObject, str | None]],
    rich_summary: Callable[[OutputDocument], str] | None = None,
    provenance: JsonObject | None = None,
    confirm_prompt: Callable[[Sequence[_PlanT]], None] | None = None,
) -> None:
    """Sequence one safe variadic deletion for already-resolved targets.

    ``targets`` are the resolved planning-preflight results.  ``build_plan``
    projects one target's immutable plan; ``execute_target`` revalidates and
    runs that target's single-target command, returning ``(ok, result, error)``
    where ``error`` is a sanitized message on failure.  ``--dry-run`` returns
    one ordered aggregate plan; otherwise one confirmation lists all targets,
    execution runs sequentially in argument order, and one document carries
    ordered per-target results.  Non-zero exit on any per-target failure.
    """
    if dry_run:
        ordered: list[JsonObject] = [
            {"target": target_id(item), "plan": build_plan(item)} for item in targets
        ]
        emit(
            success_document(
                command=command,
                result=cast("JsonObject", {"targets": ordered}),
                provenance=provenance,
                dry_run=True,
            ),
            mode,
            rich=rich_summary,
        )
        return

    if confirm_prompt is not None and mode is OutputMode.RICH and not yes:
        confirm_prompt(targets)
    elif confirm_prompt is not None and not yes:
        require_machine_confirmation(mode, yes, command=command)

    results: list[JsonObject] = []
    any_failed = False
    for item in targets:
        ok, result, error = execute_target(item)
        entry: JsonObject = {"target": target_id(item), "ok": ok, "result": result}
        if error is not None:
            entry["error"] = error
            any_failed = True
        results.append(entry)

    if any_failed:
        emit(
            failure_document(
                command=command,
                context=cast("JsonObject", {"targets": results}),
                provenance=provenance,
                dry_run=False,
                error_code=f"{command.replace('.', '_')}_partial_failure",
                error_message="one or more targets failed",
            ),
            mode,
            rich=rich_summary,
        )
        raise click.exceptions.Exit(1)

    emit(
        success_document(
            command=command,
            result=cast("JsonObject", {"targets": results}),
            provenance=provenance,
        ),
        mode,
        rich=rich_summary,
    )


def confirm_all_targets(
    targets: Sequence[_PlanT],
    *,
    target_label: Callable[[_PlanT], str],
    prompt_message: Callable[[Sequence[_PlanT]], str],
) -> None:
    """One Rich confirmation listing all sanitized targets, aborting on no."""
    rich_print(
        sanitize_terminal_text(
            "\n".join(f"- {target_label(item)}" for item in targets),
            preserve_newlines=True,
        ),
        preserve_newlines=True,
    )
    click.confirm(prompt_message(targets), default=False, abort=True)


__all__ = [
    "TargetError",
    "confirm_all_targets",
    "require_machine_confirmation",
    "run_multi_target_deletion",
]
