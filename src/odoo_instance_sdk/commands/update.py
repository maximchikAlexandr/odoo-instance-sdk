"""Click adapter for the ``odcli update`` self-upgrade flow."""

from __future__ import annotations

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
    prepare_maintenance_environment,
    run_maintenance,
    update_command,
)
from odoo_instance_sdk.models.update import UpdateOutcome


def _maintenance_exit_code() -> int | None:
    if is_maintenance_mode():
        return run_maintenance()
    return None


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
def update_command_cli(  # noqa: C901
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

    confirm = None
    if not yes and not dry_run and not check and mode is OutputMode.RICH:

        def confirm() -> None:
            click.confirm("Proceed with OdCLI update?", default=False, abort=True)

    _FAILURE_OUTCOMES: frozenset[UpdateOutcome] = frozenset(
        {
            "unsupported_install",
            "preflight_failed",
            "rolled_back",
            "update_incomplete",
        }
    )

    try:
        if check:
            command = update_command(
                ref=ref,
                check=True,
                dry_run=dry_run,
                allow_downgrade=allow_downgrade,
            )
        else:
            # Mutable refs use an explicit read-only first stage.  It is
            # captured without launching; only after it completes do we
            # capture the exact-SHA command shown to preview/confirm.
            candidate = update_command(ref=ref, allow_downgrade=allow_downgrade)
            if any(step.step_id == "update.resolve" for step in candidate.plan.steps):
                resolution = candidate.run()
                if resolution.outcome in _FAILURE_OUTCOMES:
                    fail(
                        mode,
                        "update",
                        resolution.next_step or "update resolution failed",
                        dry_run=dry_run,
                        error_code=resolution.outcome,
                        details=model_to_dict(resolution),
                    )
                target_ref = resolution.target_sha or ref
                command = update_command(
                    ref=target_ref,
                    allow_downgrade=allow_downgrade,
                )
            else:
                command = candidate

        def build_command() -> Command[UpdateResult]:
            return command

        status, result = run_or_preview(
            build_command,
            command_name="update",
            mode=mode,
            dry_run=dry_run,
            result=lambda value: model_to_dict(value) if value else {},
            confirm=confirm,
            preview=lambda command: model_to_dict(command.plan),
            emit_normal=False,
        )
    except UpdateError as exc:
        fail(mode, "update", str(exc), dry_run=dry_run)

    if result is not None:
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
    raise click.exceptions.Exit(status)


__all__ = ["update_command_cli"]
