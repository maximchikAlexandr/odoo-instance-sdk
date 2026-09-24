"""Click adapter for the ``odcli update`` self-upgrade flow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.execution import Command
    from odoo_instance_sdk.models.update import UpdateResult
else:
    import rich_click as click

from odoo_instance_sdk.commands.output import (
    OutputMode,
    emit,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    success_document,
)
from odoo_instance_sdk.exceptions import UpdateError
from odoo_instance_sdk.internal.self_update import (
    is_maintenance_mode,
    preflight_update_command,
    prepare_maintenance_environment,
    run_maintenance,
    update_command,
)
from odoo_instance_sdk.models.update import UpdateOutcome

_FAILURE_OUTCOMES: frozenset[UpdateOutcome] = frozenset(
    {
        "unsupported_install",
        "preflight_failed",
        "rolled_back",
        "update_incomplete",
    }
)


@dataclass(frozen=True, slots=True)
class _UpdateSelection:
    command: Command[UpdateResult] | None
    result: UpdateResult | None = None


def _select_update_command(
    *,
    ref: str,
    check: bool,
    allow_downgrade: bool,
) -> _UpdateSelection:
    """Resolve, preflight, and capture the command shown by the CLI."""
    if check:
        return _UpdateSelection(
            command=update_command(
                ref=ref,
                check=True,
                allow_downgrade=allow_downgrade,
            )
        )

    candidate = update_command(ref=ref, allow_downgrade=allow_downgrade)
    if not any(step.step_id == "update.resolve" for step in candidate.plan.steps):
        preflight = preflight_update_command(
            ref=ref,
            allow_downgrade=allow_downgrade,
        ).run()
        if preflight.outcome in _FAILURE_OUTCOMES:
            return _UpdateSelection(command=None, result=preflight)
        return _UpdateSelection(command=candidate)

    resolution = candidate.run()
    if resolution.outcome in _FAILURE_OUTCOMES:
        return _UpdateSelection(command=None, result=resolution)
    target_ref = resolution.target_sha or ref
    if resolution.outcome == "already_current":
        return _UpdateSelection(
            command=update_command(
                ref=target_ref,
                allow_downgrade=allow_downgrade,
            )
        )
    preflight = preflight_update_command(
        ref=target_ref,
        allow_downgrade=allow_downgrade,
    ).run()
    if preflight.outcome in _FAILURE_OUTCOMES:
        return _UpdateSelection(command=None, result=preflight)
    return _UpdateSelection(
        command=update_command(
            ref=target_ref,
            allow_downgrade=allow_downgrade,
            force_mutation=True,
        )
    )


def _require_update_command(selection: _UpdateSelection) -> Command[UpdateResult]:
    if selection.command is None:
        raise UpdateError("update command selection produced no command")
    return selection.command


def _maintenance_exit_code() -> int | None:
    if is_maintenance_mode():
        return run_maintenance()
    return None


def _require_structured_confirmation(
    *,
    mode: OutputMode,
    yes: bool,
    dry_run: bool,
    check: bool,
) -> None:
    if not yes and not dry_run and not check and mode is not OutputMode.RICH:
        fail(
            mode,
            "update",
            "structured update requires --yes to proceed",
            dry_run=False,
            error_code="update_not_confirmed",
        )


def _interactive_confirmation(
    *,
    mode: OutputMode,
    yes: bool,
    dry_run: bool,
    check: bool,
) -> Callable[[], None] | None:
    if not yes and not dry_run and not check and mode is OutputMode.RICH:

        def confirm() -> None:
            click.confirm("Proceed with OdCLI update?", default=False, abort=True)

        return confirm
    return None


def _emit_update_result(
    *,
    mode: OutputMode,
    dry_run: bool,
    result: UpdateResult | None,
) -> None:
    if result is None:
        return
    payload = model_to_dict(result)
    if result.outcome in _FAILURE_OUTCOMES:
        fail(
            mode,
            "update",
            result.next_step or "update failed",
            dry_run=dry_run,
            error_code=result.outcome,
            details=payload,
        )
    emit(success_document(command="update", result=payload), mode)


@click.command("update", help="Self-upgrade an OdCLI uv-tool install.")
@click.option(
    "--check",
    "check",
    is_flag=True,
    default=False,
    help="Resolve and report the target revision without mutating.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Resolve the target and emit the frozen non-mutating plan.",
)
@click.option(
    "--ref",
    "ref",
    default="main",
    help="Target ref (default: main). A 40-char SHA pins an exact revision.",
)
@click.option(
    "--yes",
    "yes",
    is_flag=True,
    default=False,
    help="Confirm non-interactively after preflight.",
)
@click.option(
    "--no-input",
    "no_input",
    is_flag=True,
    default=False,
    help="Forbid prompts; exits before mutations unless --yes is also set.",
)
@click.option(
    "--allow-downgrade",
    "allow_downgrade",
    is_flag=True,
    default=False,
    help="Permit installing an older revision.",
)
@output_options
def update_command_cli(
    check: bool,
    dry_run: bool,
    ref: str,
    yes: bool,
    no_input: bool,
    allow_downgrade: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Adapter that delegates to ``update_command()`` and emits one document."""
    mode = resolve_output_mode(output_format, json_output)
    if check and dry_run:
        fail(
            mode,
            "update",
            "--check and --dry-run are mutually exclusive",
            dry_run=False,
            usage=True,
        )
    _require_structured_confirmation(mode=mode, yes=yes, dry_run=dry_run, check=check)
    prepare_maintenance_environment()
    # Only an explicit maintenance environment is allowed to enter the child
    # maintenance hand-off.  A user-visible migrate journal resumes through
    # update_command(), where the coordinator owns lock and preflight checks.
    maintenance_status = _maintenance_exit_code()
    if maintenance_status is not None:
        raise click.exceptions.Exit(maintenance_status)
    if not yes and no_input and not dry_run and not check:
        fail(
            mode,
            "update",
            "non-interactive update requires --yes to proceed",
            dry_run=False,
            error_code="update_not_confirmed",
        )

    confirm = _interactive_confirmation(mode=mode, yes=yes, dry_run=dry_run, check=check)

    try:
        selection = _select_update_command(
            ref=ref,
            check=check,
            allow_downgrade=allow_downgrade,
        )
        if selection.result is not None:
            fail(
                mode,
                "update",
                selection.result.next_step or "update resolution failed",
                dry_run=dry_run,
                error_code=selection.result.outcome,
                details=model_to_dict(selection.result),
            )
        command = _require_update_command(selection)

        def build_command() -> Command[UpdateResult]:
            return command

        status, result = run_or_preview(
            build_command,
            command_name="update",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value else {},
            confirm=confirm,
            preview=lambda selected: model_to_dict(selected.plan),
            emit_normal=False,
        )
    except UpdateError as exc:
        fail(mode, "update", str(exc), dry_run=dry_run)

    _emit_update_result(mode=mode, dry_run=dry_run, result=result)
    raise click.exceptions.Exit(status)


__all__ = ["update_command_cli"]
