from __future__ import annotations

import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import TypeVar, cast

import rich_click as click

from odoo_instance_sdk.commands.cli_parts.registration import _RunCommand, cli
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    bordered_table,
    command_options,
    fail,
    model_to_dict,
    render_rich_text,
    resolve_command_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.cli_format import rich_cell
from odoo_instance_sdk.internal.proc import PreparedAction, RunContext
from odoo_instance_sdk.internal.project_manifest import manifest_path
from odoo_instance_sdk.internal.project_runtime import (
    ProjectHttpPortSelection,
    apply_project_http_port_selection,
    prepare_project_http_port_selection,
    select_project_http_port,
)
from odoo_instance_sdk.project import ProjectConfig

_ResultT = TypeVar("_ResultT")


def _with_project_http_port_action(
    command: Command[_ResultT], selection: ProjectHttpPortSelection
) -> Command[_ResultT]:
    prepared = command._prepared()
    prepared_action = PreparedAction(
        step_id="project.http_port.revalidate",
        action="project_http_port.revalidate_and_repair_owned_config",
        description="Revalidate the captured endpoint and repair owned config if needed",
        details={
            "port": selection.port,
            "selection": selection.selection_kind,
            "live_assignment": "persisted_before_capture",
            "owned_config_repair": "if_drifted",
        },
        mutating=True,
    )
    action = prepared_action.public_projection()

    def execute(context: RunContext[_ResultT]) -> _ResultT:
        context.action(action.step_id)
        apply_project_http_port_selection(selection, None)
        context.complete_action(action.step_id)
        return prepared.callback(context)

    plan = ExecutionPlan(
        steps=(action, *command.plan.steps),
        observations=command.plan.observations,
        warnings=command.plan.warnings,
    ).with_fingerprint()
    return Command.create(
        plan,
        execute,
        (prepared_action, *prepared.steps),
        executor=prepared.executor,
        private_projection=prepared.private_projection,
    )


@cli.command(
    cls=_RunCommand,
    help="Native Odoo arguments must follow a literal `--` delimiter.",
    short_help="Start resolved Odoo in the foreground or detached.",
)
@click.option(
    "-d",
    "--detach",
    "detach",
    is_flag=True,
    default=False,
    help="Launch Odoo detached and return once it is alive.",
)
@click.option(
    "--wait-ready",
    is_flag=True,
    default=False,
    help="Wait for the environment-bound detached runtime to pass readiness checks.",
)
@click.option(
    "--readiness-timeout",
    type=click.FloatRange(min=0.0, min_open=True),
    default=None,
    help="Finite readiness wait in seconds (default: 60 when enabled).",
)
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@command_options
@pass_cli_context
def run(  # noqa: C901
    ctx: CliContext,
    detach: bool,
    wait_ready: bool,
    readiness_timeout: float | None,
    odoo_args: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    from odoo_instance_sdk.commands.cli_parts.callbacks import _ready_instance

    if wait_ready and (not detach or (ctx.env is None and ctx.project is None)):
        raise click.UsageError(
            "--wait-ready requires root --env or --project together with --detach"
        )
    if not wait_ready and readiness_timeout is not None:
        raise click.UsageError("--readiness-timeout requires --wait-ready")
    effective_readiness_timeout = 60.0 if readiness_timeout is None else readiness_timeout
    if detach:
        output_mode = resolve_output_mode(output_format, json_output)
    else:
        output_mode = resolve_command_options(output_format, json_output, dry_run, command="run")
    try:
        runtime_context = _ready_instance(ctx)
        port_selection: ProjectHttpPortSelection | None = None
        if (
            not runtime_context.is_environment
            and manifest_path(runtime_context.project_root).is_file()
        ):
            project = cast("ProjectConfig", runtime_context.source)
            port_selection = (
                select_project_http_port(project, None)
                if dry_run
                else prepare_project_http_port_selection(project, None)
            )
            runtime_context = replace(
                runtime_context,
                source=port_selection.project,
                instance=runtime_context.client.instance.from_project(port_selection.project),
            )
        pg_dump = shutil.which("pg_dump") if os.name != "nt" else None
        if pg_dump is not None:
            pg_dump = str(Path(pg_dump).resolve())
        shim_dir = Path(__file__).resolve().parents[2] / "internal" / "pg_dump_compat"
        pg_dump_env = (
            {
                "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "ODCLI_REAL_PG_DUMP": pg_dump,
            }
            if pg_dump is not None
            else None
        )
        if detach:
            detached_command = runtime_context.instance.run_detached_command(
                args=odoo_args,
                env=pg_dump_env,
                wait_ready=wait_ready,
                readiness_timeout=effective_readiness_timeout,
                http_port_selection=(
                    port_selection.selection_kind if port_selection is not None else None
                ),
                http_port_fallback_reason=(
                    port_selection.fallback_reason if port_selection is not None else None
                ),
            )
            if port_selection is not None:
                detached_command = _with_project_http_port_action(detached_command, port_selection)
        else:
            if not dry_run:
                available = runtime_context.check_port_free()
                if runtime_context.is_environment:
                    env = runtime_context.require_environment()
                    detail = (
                        f"{env.http_interface}:{env.http_port} is available"
                        if available
                        else f"{env.http_interface}:{env.http_port} is occupied"
                    )
                else:
                    http_interface, http_port = runtime_context.instance_address()
                    detail = (
                        f"{http_interface}:{http_port} is available"
                        if available
                        else f"{http_interface}:{http_port} is occupied (ownership unknown)"
                    )
                if not available:
                    fail(output_mode, "run", f"port-conflict: {detail}", dry_run=dry_run)
            foreground_command = runtime_context.instance.run_foreground_command(
                args=odoo_args, env=pg_dump_env
            )
            if port_selection is not None:
                foreground_command = _with_project_http_port_action(
                    foreground_command, port_selection
                )
    except SystemExit:
        raise
    except Exception as e:
        fail(
            output_mode,
            "run",
            e,
            dry_run=dry_run,
            error_code=getattr(e, "error_code", getattr(e, "code", None)),
        )
    if not dry_run and runtime_context.is_environment:
        runtime_context.client.environments.record_use(runtime_context.require_environment())
    if detach:
        try:
            status, _value = run_or_preview(
                lambda: detached_command,
                command_name="run",
                mode=output_mode,
                dry_run=dry_run,
                result=lambda result: (
                    model_to_dict(result) if result is not None else cast("JsonObject", {})
                ),
                provenance=cast("JsonObject", runtime_context.output_provenance),
                rich=_rich_detached_status,
            )
        except SystemExit:
            raise
        except Exception as e:
            fail(
                output_mode,
                "run",
                e,
                dry_run=dry_run,
                error_code=getattr(e, "error_code", getattr(e, "code", None)),
            )
        if not dry_run:
            sys.exit(status)
        return
    try:
        warnings = (
            (
                (
                    f"HTTP port {port_selection.requested_port or 8069} is occupied; "
                    f"using {port_selection.port} (automatic fallback)."
                ),
            )
            if port_selection is not None and port_selection.fallback_reason is not None
            else ()
        )
        _status, exit_code = run_or_preview(
            lambda: foreground_command,
            command_name="run",
            mode=output_mode,
            dry_run=dry_run,
            emit_normal=False,
            warnings=warnings,
            warnings_before_execution=True,
        )
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        fail(
            output_mode,
            "run",
            e,
            dry_run=dry_run,
            error_code=getattr(e, "error_code", getattr(e, "code", None)),
        )
    if not dry_run:
        sys.exit(int(exit_code or 0))
    return


def _rich_detached_status(document: OutputDocument) -> str:
    result = document.result
    if not isinstance(result, dict):
        table = bordered_table("Field", "Value", title="Detached launch")
        table.add_row("Status", "planned")
        return render_rich_text(table)
    table = bordered_table("Field", "Value", title="Detached launch")
    for field, label in (
        ("pid", "PID"),
        ("http_endpoint", "Endpoint"),
        ("http_port", "HTTP port"),
        ("http_port_selection", "Port selection"),
        ("http_port_fallback_reason", "Fallback reason"),
        ("owner_kind", "Owner kind"),
        ("owner_id", "Owner ID"),
        ("log_path", "Log path"),
    ):
        if field in result:
            table.add_row(label, rich_cell(result[field]))
    if not table.rows:
        table.add_row("Status", "planned")
    return render_rich_text(table)
