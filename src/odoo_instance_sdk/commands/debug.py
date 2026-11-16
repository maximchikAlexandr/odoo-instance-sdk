"""CLI adapters for the headless Odoo debug session."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.commands.context import CliContext, pass_cli_context, ready_instance
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    bordered_table,
    fail,
    model_to_dict,
    output_options,
    render_rich_text,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.exceptions import DebugError
from odoo_instance_sdk.execution import Command, JsonValue
from odoo_instance_sdk.models import DevelopmentEnvironment
from odoo_instance_sdk.models.debug import DebugSnapshot, DebugValue
from odoo_instance_sdk.resources.debug import DebugResource

_HELP = (
    "One persistent headless session per environment. It keeps running after "
    "the command returns; Ctrl+C cancels only the current wait. Requires "
    "workers = 0, no reload, and debugpy in the recorded environment. "
    "eval executes arbitrary code in that trusted local process."
)


@click.group("debug", help=_HELP)
def debug_group() -> None:
    """Expose one non-interactive debug intention per invocation."""


def _rich(document: OutputDocument) -> str:
    """Render one debug snapshot or value as a bounded table."""
    if not document.ok:
        return document.error.message if document.error is not None else "debug failed"
    value = document.result if isinstance(document.result, dict) else {}
    table = bordered_table("Field", "Value", title="Debug")
    for key in ("session_id", "state", "stop_reason", "expression", "value", "type"):
        if key in value and value[key] not in (None, ""):
            table.add_row(key, str(value[key]))
    location = value.get("location")
    if isinstance(location, dict):
        table.add_row("location", f"{location.get('path')}:{location.get('line')}")
    return render_rich_text(table)


def _resource(ctx: CliContext) -> DebugResource:
    """Resolve a ready environment before any session file is created."""
    resolved = ready_instance(ctx)
    if not resolved.is_environment:
        raise DebugError(
            "environment-required",
            "debug requires a ready environment",
            hint="Pass --env or run inside an environment worktree",
        )
    return DebugResource(resolved.instance, cast("DevelopmentEnvironment", resolved.source))


def _run(
    command_name: str,
    mode: OutputMode,
    dry_run: bool,
    build: Callable[[], Command[DebugSnapshot] | Command[DebugValue]],
) -> None:
    try:
        status, _value = run_or_preview(
            build,
            command_name=command_name,
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value is not None else {},
            rich=_rich,
            on_interrupt=lambda _exc: None,
        )
    except click.exceptions.Exit:
        raise
    except click.ClickException:
        raise
    except DebugError as exc:
        fail(
            mode,
            command_name,
            exc,
            dry_run=dry_run,
            error_code=exc.code,
            details=_error_details(exc),
        )
    except Exception as exc:
        code = getattr(exc, "code", None)
        fail(
            mode,
            command_name,
            exc,
            dry_run=dry_run,
            error_code=code if isinstance(code, str) else None,
        )
    raise click.exceptions.Exit(status)


def _error_details(exc: DebugError) -> JsonObject | None:
    details: dict[str, JsonValue] = {}
    if exc.hint is not None:
        details["hint"] = exc.hint
    if exc.state is not None:
        details["state"] = exc.state
    if exc.snapshot is not None:
        details["snapshot"] = cast("JsonValue", model_to_dict(exc.snapshot))
    return details or None


def _checked_wait(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= 0 or value > 300:
        raise click.BadParameter("must be greater than 0 and at most 300")
    return value


@debug_group.command("start", help=_HELP)
@click.option("--break", "breakpoints", multiple=True, help="FILE:LINE[:CONDITION]")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@output_options
@pass_cli_context
def debug_start(
    ctx: CliContext,
    breakpoints: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Start one session, or show its plan."""
    mode = resolve_output_mode(output_format, json_output)
    _run(
        "debug.start",
        mode,
        dry_run,
        lambda: _resource(ctx).start_command(breakpoints=breakpoints),
    )


@debug_group.command("context", help="Show the current snapshot. --wait does not resume.")
@click.option("--wait", type=float, default=None, help="Seconds to wait without resuming.")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@output_options
@pass_cli_context
def debug_context(
    ctx: CliContext,
    wait: float | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Return the current session snapshot."""
    checked = _checked_wait(wait)
    mode = resolve_output_mode(output_format, json_output)
    _run(
        "debug.context",
        mode,
        dry_run,
        lambda: _resource(ctx).context_command(wait=checked),
    )


@debug_group.command("continue", help="Resume a stopped target. Ctrl+C cancels only this wait.")
@click.option("--wait", type=float, default=None, help="Seconds to wait after resuming.")
@click.option("--break", "breakpoints", multiple=True, help="FILE:LINE[:CONDITION] to add.")
@click.option("--remove-break", "remove", multiple=True, help="FILE:LINE to remove.")
@click.option("--clear-breaks", is_flag=True, help="Remove every breakpoint first.")
@click.option("--to", "temporary", default=None, help="One-shot FILE:LINE breakpoint.")
@click.argument("trigger", nargs=-1)
@click.option("--dry-run", is_flag=True, help="Plan only.")
@output_options
@pass_cli_context
def debug_continue(
    ctx: CliContext,
    wait: float | None,
    breakpoints: tuple[str, ...],
    remove: tuple[str, ...],
    clear_breaks: bool,
    temporary: str | None,
    trigger: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Apply one continue intention."""
    checked = _checked_wait(wait)
    mode = resolve_output_mode(output_format, json_output)
    _run(
        "debug.continue",
        mode,
        dry_run,
        lambda: _resource(ctx).continue_session_command(
            wait=checked,
            breakpoints=breakpoints,
            remove=remove,
            clear=clear_breaks,
            temporary=temporary,
            trigger=trigger,
        ),
    )


@debug_group.command("step", help="Step over, in, or out, then wait for the next stop.")
@click.argument("kind", type=click.Choice(("over", "in", "out")))
@click.option("--timeout", type=float, default=30, show_default=True)
@click.option("--dry-run", is_flag=True, help="Plan only.")
@output_options
@pass_cli_context
def debug_step(
    ctx: CliContext,
    kind: str,
    timeout: float,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Step the stopped target once."""
    checked = _checked_wait(timeout)
    mode = resolve_output_mode(output_format, json_output)
    _run(
        "debug.step",
        mode,
        dry_run,
        lambda: _resource(ctx).step_command(kind, timeout=checked or timeout),
    )


@debug_group.command("pause", help="Pause the running target and wait for the stop.")
@click.option("--timeout", type=float, default=30, show_default=True)
@click.option("--dry-run", is_flag=True, help="Plan only.")
@output_options
@pass_cli_context
def debug_pause(
    ctx: CliContext,
    timeout: float,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Request one pause."""
    checked = _checked_wait(timeout)
    mode = resolve_output_mode(output_format, json_output)
    _run(
        "debug.pause",
        mode,
        dry_run,
        lambda: _resource(ctx).pause_command(timeout=checked or timeout),
    )


@debug_group.command(
    "eval",
    help=(
        "Evaluate one expression in the stopped process. "
        "This executes arbitrary code in the trusted local Odoo process."
    ),
)
@click.argument("expression")
@click.option("--frame", type=int, default=0, show_default=True)
@click.option("--depth", type=click.IntRange(0, 3), default=0, show_default=True)
@click.option("--dry-run", is_flag=True, help="Plan only.")
@output_options
@pass_cli_context
def debug_eval(
    ctx: CliContext,
    expression: str,
    frame: int,
    depth: int,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Evaluate one expression while the target is stopped."""
    mode = resolve_output_mode(output_format, json_output)
    _run(
        "debug.eval",
        mode,
        dry_run,
        lambda: _resource(ctx).eval_command(expression, frame=frame, depth=depth),
    )


@debug_group.command("restart", help="Relaunch the target and keep session breakpoints.")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@output_options
@pass_cli_context
def debug_restart(
    ctx: CliContext,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Restart the same session."""
    mode = resolve_output_mode(output_format, json_output)
    _run("debug.restart", mode, dry_run, lambda: _resource(ctx).restart_command())


@debug_group.command("stop", help="Stop the session and remove its private files.")
@click.option("--dry-run", is_flag=True, help="Plan only.")
@output_options
@pass_cli_context
def debug_stop(
    ctx: CliContext,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Stop the daemon, target, and trigger."""
    mode = resolve_output_mode(output_format, json_output)
    _run("debug.stop", mode, dry_run, lambda: _resource(ctx).stop_command())
