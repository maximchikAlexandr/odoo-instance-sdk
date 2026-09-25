from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Hashable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from click.shell_completion import CompletionItem

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.cli_parts.init_registration import _OptionState
from odoo_instance_sdk.commands.cli_parts.registration import (
    _run_shell_command,
    _RunCommand,
    _ShellCommandFailure,
    cli,
)
from odoo_instance_sdk.commands.context import (
    CliContext,
    ResolvedContext,
    pass_cli_context,
)
from odoo_instance_sdk.commands.module import register_module_commands
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    action_command,
    bordered_table,
    command_options,
    emit,
    emit_json_envelope,
    fail,
    failure_document,
    model_to_dict,
    output_options,
    render_rich_text,
    resolve_command_options,
    resolve_output_mode,
    rich_print,
    run_or_preview,
    sanitize_diagnostic,
    success_document,
)
from odoo_instance_sdk.commands.pg import (
    postgres_group as _postgres_group,
)
from odoo_instance_sdk.commands.translations import register_translation_commands
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    LogfileAccessError,
)
from odoo_instance_sdk.internal.automation import (
    eval_expression_command,
    exec_script_command,
)
from odoo_instance_sdk.internal.cli_format import rich_cell
from odoo_instance_sdk.internal.generated_config import (
    project_generated_config_path,
)
from odoo_instance_sdk.internal.project_init import (
    generated_config_needs_repair,
    manifest_dict,
    register_initialized_project,
    validate_generated_config_target,
    write_project_generated_config,
)
from odoo_instance_sdk.internal.vscode_generate import (
    build_launch_profile,
    launch_json,
    write_launch_json,
)
from odoo_instance_sdk.models import (
    DepsVerifyResult,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.deps import verify_deps_command

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.doctor import CheckResult, DoctorReport


def _ready_instance(ctx: CliContext) -> ResolvedContext:
    return cli_context.ready_instance(ctx)


def _handle_existing_manifest(  # noqa: C901
    existing: Path,
    resolved_project: Path,
    config: ProjectConfig,
    no_input: bool,
    yes: bool,
    output_mode: OutputMode,
    *,
    dry_run: bool,
) -> bool:
    try:
        existing_cfg = ProjectConfig.load(resolved_project)
    except Exception as e:
        fail(output_mode, "init", f"Existing manifest unreadable: {e}", dry_run=dry_run)
    # Comparison excludes ``postgres_allocated`` (dry-run-only flag); both
    # sides default to False here.
    if manifest_dict(existing_cfg) == manifest_dict(config):
        target = project_generated_config_path(resolved_project)
        repair = generated_config_needs_repair(resolved_project, existing_cfg)
        if repair:
            if dry_run:
                result: JsonObject = {
                    **manifest_dict(config),
                    "generated_config": cast("JsonValue", {"path": str(target), "repair": True}),
                }
                if output_mode is not OutputMode.RICH:
                    emit_json_envelope(
                        ok=True,
                        command="init",
                        result=result,
                        provenance={},
                        dry_run=True,
                        mode=output_mode,
                    )
                else:
                    rich_print(f"Dry run — generated config needs repair: {target}")
                return True
            try:
                validate_generated_config_target(target, project_root=resolved_project)
            except InstanceConfigurationError as exc:
                fail(output_mode, "init", str(exc), dry_run=dry_run)
            write_project_generated_config(resolved_project, existing_cfg)
            register_initialized_project(resolved_project)
            if output_mode is not OutputMode.RICH:
                emit_json_envelope(
                    ok=True,
                    command="init",
                    result={
                        **manifest_dict(config),
                        "generated_config": {"path": str(target), "repaired": True},
                    },
                    provenance={},
                    mode=output_mode,
                )
            else:
                rich_print(f"Repaired generated config: {target}; manifest unchanged.")
            return True
        if not dry_run:
            register_initialized_project(resolved_project)
        if output_mode is not OutputMode.RICH:
            emit_json_envelope(
                ok=True,
                command="init",
                result=manifest_dict(config),
                provenance={},
                dry_run=True,
                mode=output_mode,
            )
        else:
            rich_print("Manifest already up to date; no-op.")
        return True
    if no_input or output_mode is not OutputMode.RICH:
        if yes:
            return False
        fail(
            output_mode,
            "init",
            "manifest exists and differs; remove it first or adjust options",
            dry_run=dry_run,
        )
    if yes:
        return False
    if not click.confirm("Manifest exists and differs; overwrite?", default=False):
        rich_print("Aborted.")
        return True
    return False


@cli.command(help="Diagnose project, runtime, and PostgreSQL.")
@output_options
@pass_cli_context
def doctor(ctx: CliContext, output_format: str | None, json_output: bool) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        resolved = cli_context._ready_instance_for_doctor(ctx)
        from odoo_instance_sdk.internal.doctor import run_doctor

        report = run_doctor(
            resolved.client,
            resolved.project_root,
            resolved_context=resolved,
        )
    except Exception as e:
        fail(output_mode, "doctor", str(e), dry_run=False)
    if json_output:
        emit_json_envelope(
            ok=report.ok,
            command="doctor",
            context=cast("JsonObject", report.context),
            result={
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status,
                        "detail": sanitize_diagnostic(c.detail),
                        "environment_id": c.environment_id,
                        "environment_name": c.environment_name,
                        "remediations": [item.as_dict() for item in c.remediations],
                        "facts": c.facts,
                    }
                    for c in report.checks
                ],
                "drift": cast("JsonValue", [item.as_dict() for item in report.drift]),
            },
            error_code="doctor_failed" if not report.ok else None,
            error_message="doctor reported failed checks" if not report.ok else None,
            mode=output_mode,
        )
    else:
        _print_doctor(report)
    sys.exit(0 if report.ok else 1)


def _print_doctor(report: DoctorReport) -> None:
    grouped: dict[str, list[CheckResult]] = {}
    group_titles: dict[str, str] = {}
    for check in report.checks:
        scope = _doctor_check_scope(check)
        key = check.environment_id or scope
        grouped.setdefault(key, []).append(check)
        group_titles[key] = (
            f"Environment: {check.environment_name or check.environment_id}"
            if check.environment_id
            else f"{scope.title()} checks"
        )
    for key, checks in grouped.items():
        table = bordered_table("Check", "Status", "Details", title=group_titles[key])
        for check in checks:
            detail_parts = [sanitize_diagnostic(check.detail)]
            if check.facts:
                detail_parts.append(
                    "facts="
                    + json.dumps(check.facts, ensure_ascii=False, sort_keys=True, default=str)
                )
            for remediation in check.remediations:
                detail_parts.append(
                    "remediation="
                    + sanitize_diagnostic(remediation.description)
                    + f" argv={list(remediation.argv)!r}"
                    + f" mutating={remediation.mutating}"
                    + f" dry_run_supported={remediation.dry_run_supported}"
                )
            table.add_row(
                rich_cell(check.name),
                rich_cell(
                    {"ok": "OK", "warn": "WARN", "error": "ERROR", "info": "INFO"}.get(
                        check.status, check.status
                    )
                ),
                rich_cell("; ".join(detail_parts)),
            )
        rich_print(render_rich_text(table), preserve_newlines=True)
    for drift in report.drift:
        table = bordered_table(
            "Component", "Status", "Details", title=f"{drift.environment_id} drift"
        )
        for component in drift.components:
            table.add_row(
                rich_cell(component.component),
                rich_cell(component.status.upper()),
                rich_cell(
                    f"{sanitize_diagnostic(component.reason)} "
                    f"({sanitize_diagnostic(component.remediation)})"
                ),
            )
        context = drift.git_context
        table.add_row(
            "GIT",
            "CONTEXT",
            rich_cell(
                f"dirty={context.get('dirty')} ahead={context.get('ahead')} "
                f"behind={context.get('behind')}"
            ),
        )
        rich_print(render_rich_text(table), preserve_newlines=True)


_GLOBAL_DOCTOR_CHECKS = frozenset({"uv", "msgfmt", "git-absorb", "catalog", "orphaned"})


def _doctor_check_scope(check: CheckResult) -> str:
    """Derive display scope without extending the machine doctor schema."""
    if check.environment_id is not None:
        return "environment"
    return "global" if check.name in _GLOBAL_DOCTOR_CHECKS else "project"


@cli.command(help="Stop the selected runtime.")
@command_options
@pass_cli_context
def stop(
    ctx: CliContext,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = _ready_instance(ctx)
        runtime = runtime_context.runtime
        command = runtime_context.instance.stop_runtime_command()
        stop_context: JsonObject = {
            "owner_kind": runtime.owner_kind,
            "owner_id": runtime.owner_id,
            "project_id": runtime.project_id,
            "environment_id": runtime.environment_id,
            "environment_name": runtime.environment_name,
            "worktree_path": str(runtime.root),
        }
        rich_message = (
            f"Stopped {runtime.owner_kind} runtime owner_kind={runtime.owner_kind} "
            f"owner_id={runtime.owner_id} project_id={runtime.project_id} "
            f"environment_id={runtime.environment_id} "
            f"environment_name={runtime.environment_name}"
        )

        def stop_result(value: dict[str, str | None] | None) -> JsonObject:
            return {**stop_context, **(value or {})}
    except SystemExit:
        raise
    except Exception as error:
        fail(output_mode, "stop", error, dry_run=dry_run)
    try:
        status, _value = run_or_preview(
            lambda: command,
            command_name="stop",
            mode=output_mode,
            dry_run=dry_run,
            result=stop_result,
            context=stop_context,
            provenance=cast("JsonObject", runtime_context.output_provenance),
            rich=lambda _document: rich_message,
        )
    except SystemExit:
        raise
    except Exception as error:
        fail(output_mode, "stop", error, dry_run=dry_run)
    if not dry_run:
        sys.exit(status)


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
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@command_options
@pass_cli_context
def run(  # noqa: C901
    ctx: CliContext,
    detach: bool,
    odoo_args: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    if detach:
        output_mode = resolve_output_mode(output_format, json_output)
    else:
        output_mode = resolve_command_options(output_format, json_output, dry_run, command="run")
    try:
        runtime_context = _ready_instance(ctx)
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
                args=odoo_args, env=pg_dump_env
            )
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
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "run", e, dry_run=dry_run)
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
                error_code=getattr(e, "error_code", None),
            )
        if not dry_run:
            sys.exit(status)
        return
    try:
        _status, exit_code = run_or_preview(
            lambda: foreground_command,
            command_name="run",
            mode=output_mode,
            dry_run=dry_run,
            emit_normal=False,
        )
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        fail(output_mode, "run", e, dry_run=dry_run)
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
        ("owner_kind", "Owner kind"),
        ("owner_id", "Owner ID"),
        ("log_path", "Log path"),
    ):
        if field in result:
            table.add_row(label, rich_cell(result[field]))
    if not table.rows:
        table.add_row("Status", "planned")
    return render_rich_text(table)


@cli.command(help="Read or follow retained Odoo logs.")
@click.option("-n", "--tail", type=click.IntRange(min=1), default=100, show_default=True)
@click.option("-f", "--follow", is_flag=True, default=False)
@pass_cli_context
def logs(ctx: CliContext, tail: int, follow: bool) -> None:
    try:
        runtime_context = _ready_instance(ctx)
        for line in runtime_context.instance.iter_logs(tail=tail, follow=follow):
            sys.stdout.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        sys.exit(130)
    except LogfileAccessError as e:
        fail(OutputMode.RICH, "logs", str(e), dry_run=False)
    except InstanceConfigurationError as e:
        fail(OutputMode.RICH, "logs", str(e), dry_run=False)
    except Exception as e:
        fail(OutputMode.RICH, "logs", str(e), dry_run=False)


@cli.command(help="Open an interactive Odoo shell.")
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@command_options
@pass_cli_context
def shell(
    ctx: CliContext,
    odoo_args: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_command_options(output_format, json_output, dry_run, command="shell")
    try:
        runtime_context = _ready_instance(ctx)
        command = runtime_context.instance.shell_command(args=list(odoo_args))
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "shell", e, dry_run=dry_run)
    try:
        _status, value = run_or_preview(
            lambda: command,
            command_name="shell",
            mode=output_mode,
            dry_run=dry_run,
            emit_normal=False,
        )
    except KeyboardInterrupt:
        value = 130
    except Exception as e:
        fail(output_mode, "shell", e, dry_run=dry_run)
    if not dry_run:
        sys.exit(int(value or 0))
    return


@cli.command("eval", help="Evaluate a Python expression in Odoo.")
@click.argument("expression")
@click.option(
    "--commit", "commit", is_flag=True, default=False, help="Commit after eval (best-effort)."
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def eval_cmd(
    ctx: CliContext,
    expression: str,
    commit: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = _ready_instance(ctx)
        instance = runtime_context.instance
        status = _run_shell_command(
            command_name="eval",
            build_command=lambda: eval_expression_command(instance, expression, commit=commit),
            mode=output_mode,
            dry_run=dry_run,
            project_result=lambda _value, payload: {**payload, "returncode": 0},
            commit=commit,
        )
    except SystemExit:
        raise
    except _ShellCommandFailure as e:
        fail(output_mode, "eval", e, dry_run=dry_run, error_code=e.error_code, details=e.details)
    except Exception as e:
        fail(output_mode, "eval", e, dry_run=dry_run)
    sys.exit(status)


@cli.command("exec", help="Execute a Python script in Odoo.")
@click.argument("script")
@click.argument("script_args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--commit", "commit", is_flag=True, default=False, help="Commit after exec (best-effort)."
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def exec_cmd(
    ctx: CliContext,
    script: str,
    script_args: tuple[str, ...],
    commit: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    if script == "-":
        source = sys.stdin.read()
    else:
        p = Path(script)
        if not p.is_file():
            fail(output_mode, "exec", f"script not found: {script}", dry_run=dry_run)
        try:
            source = p.read_text(encoding="utf-8")
        except OSError as e:
            fail(output_mode, "exec", f"cannot read script: {e}", dry_run=dry_run)
    try:
        runtime_context = _ready_instance(ctx)
        instance = runtime_context.instance
        status = _run_shell_command(
            command_name="exec",
            build_command=lambda: exec_script_command(
                instance, source, argv=tuple(script_args), commit=commit
            ),
            mode=output_mode,
            dry_run=dry_run,
            project_result=lambda value, payload: {
                "returncode": 0,
                "stdout": payload["user_stdout"],
                "stderr": sanitize_diagnostic(value.stderr) if value.stderr else "",
                **payload,
            },
            commit=commit,
        )
    except SystemExit:
        raise
    except _ShellCommandFailure as e:
        fail(output_mode, "exec", e, dry_run=dry_run, error_code=e.error_code, details=e.details)
    except Exception as e:
        fail(output_mode, "exec", e, dry_run=dry_run)
    sys.exit(status)


@cli.group("deps", help="Verify Python and add-on dependencies.")
def deps_group() -> None:
    pass


@deps_group.command("verify", help="Check Python and add-on dependencies.")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def deps_verify(
    ctx: CliContext, dry_run: bool, output_format: str | None, json_output: bool
) -> None:
    from odoo_instance_sdk.internal.project_runtime import is_uv_python_selector

    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = _ready_instance(ctx)
        project_python = (
            runtime_context.source.python
            if isinstance(runtime_context.source, ProjectConfig)
            else None
        )
        recorded_python = (
            cast("str", project_python)
            if is_uv_python_selector(project_python)
            else runtime_context.python_path()
        )
        deferred_runtime = getattr(runtime_context.instance.config, "deferred_runtime", None)
        uv_executable = getattr(deferred_runtime, "uv_executable", "uv")
        status, _result = run_or_preview(
            lambda: verify_deps_command(
                recorded_python=recorded_python,
                worktree_root=runtime_context.worktree_path(),
                uv_executable=uv_executable,
            ),
            command_name="deps.verify",
            mode=output_mode,
            dry_run=dry_run,
            emit_normal=False,
        )
        if dry_run:
            sys.exit(status)
        if _result is None:
            fail(
                output_mode,
                "deps.verify",
                "dependency verification returned no result",
                dry_run=dry_run,
            )
        result_payload = _deps_verify_payload(_result)
        if _result.ok:
            document = success_document(command="deps.verify", result=result_payload)
        else:
            document = failure_document(
                command="deps.verify",
                dry_run=dry_run,
                error_code="deps_verify_failed",
                error_message="Dependency verification failed",
                error_details=result_payload,
            )
        status = emit(document, output_mode, rich=_rich_deps_projection)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "deps.verify", e, dry_run=dry_run)
    sys.exit(status)


def _deps_verify_payload(result: DepsVerifyResult) -> JsonObject:
    return {
        "distributions": [
            {"detail": sanitize_diagnostic(item.detail)} for item in result.distributions
        ],
        "missing_imports": [
            {
                "module": sanitize_diagnostic(item.module),
                "import": sanitize_diagnostic(item.import_name),
            }
            for item in result.missing_imports
        ],
        "pip_check_ok": result.pip_check_ok,
        "pip_check_output": sanitize_diagnostic(result.pip_check_output),
    }


def _rich_deps_projection(document: OutputDocument) -> str:
    title = "pip check: ok" if document.ok else "pip check: issues"
    table = bordered_table("Check", "Status", "Details", title=title)
    details = document.error.details if document.error is not None else None
    if document.ok:
        table.add_row("pip check", "OK", "no dependency issues")
    elif not isinstance(details, dict):
        table.add_row("pip check", "ERROR", "dependency verification failed")
    else:
        distributions = details.get("distributions", [])
        if isinstance(distributions, list):
            for item in distributions:
                if isinstance(item, dict):
                    table.add_row("distribution", "ERROR", rich_cell(item.get("detail", "")))
        missing_imports = details.get("missing_imports", [])
        if isinstance(missing_imports, list):
            for item in missing_imports:
                if isinstance(item, dict):
                    table.add_row(
                        "missing import",
                        "ERROR",
                        rich_cell(f"{item.get('module')} ({item.get('import')})"),
                    )
        if not table.rows:
            table.add_row("pip check", "ERROR", "dependency verification failed")
    return render_rich_text(table)


@cli.group("vscode", help="Generate VS Code launch configuration.")
def vscode_group() -> None:
    pass


def _rich_vscode_generate(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    table = bordered_table("Field", "Value", title="VS Code launch")
    if "written" in result:
        table.add_row("Output", rich_cell(result["written"]))
    profile = result.get("profile")
    if isinstance(profile, dict):
        table.add_row("Name", rich_cell(profile.get("name", "default")))
        table.add_row("Program", rich_cell(profile.get("program", "odoo")))
    if not table.rows:
        table.add_row("Status", "ready")
    return render_rich_text(table)


@vscode_group.command("generate", help="Generate a VS Code debugpy launch profile.")
@click.option(
    "--write", "write_file", is_flag=True, default=False, help="Write .vscode/launch.json."
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def vscode_generate(
    ctx: CliContext,
    write_file: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        runtime_context = _ready_instance(ctx)
        runtime = runtime_context.runtime

        def operation() -> dict[str, JsonValue]:
            profile = build_launch_profile(runtime)
            if write_file:
                project_path = runtime.repository_root
                written = write_launch_json(project_path, launch_json(profile))
                return {"profile": profile, "written": str(written)}
            return {"profile": profile}

        status, _result = run_or_preview(
            lambda: action_command(
                "vscode.generate",
                operation,
                description="Generate VS Code launch configuration",
                mutating=write_file,
            ),
            command_name="vscode.generate",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda value: cast("dict[str, JsonValue]", value or {}),
            rich=_rich_vscode_generate,
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "vscode.generate", e, dry_run=dry_run)
    sys.exit(status)


postgres_group = _postgres_group


class _AliasCommandMap(dict[str, click.Command]):
    """Keep compatibility names addressable without exposing duplicate rows."""

    def __init__(self, commands: Mapping[str, click.Command]) -> None:
        super().__init__(commands)
        self._aliases: dict[str, str] = {}

    def add_alias(self, alias: str, canonical: str) -> None:
        self._aliases[alias] = canonical

    def __contains__(self, name: Hashable) -> bool:
        return dict.__contains__(self, name) or name in self._aliases

    def __getitem__(self, name: str) -> click.Command:
        return dict.__getitem__(self, self._aliases.get(name, name))


def _register_short_alias(group_name: str, canonical: str, alternate: str) -> None:
    """Promote a short spelling at the composition boundary without editing leaves."""
    group = cli.commands.get(group_name)
    if not isinstance(group, click.Group):
        raise TypeError(f"CLI group is not registered: {group_name}")
    command = group.commands.get(canonical) or group.commands.get(alternate)
    if command is None:
        raise RuntimeError(f"CLI command is not registered: {group_name} {alternate}")
    group.commands.pop(canonical, None)
    group.commands.pop(alternate, None)
    command.name = canonical
    setattr(command, "aliases", [alternate])
    if not isinstance(group.commands, _AliasCommandMap):
        group.commands = _AliasCommandMap(group.commands)
    group.add_command(command, name=canonical)
    group.commands.add_alias(alternate, canonical)
    getattr(group, "_handle_extras_add_command")(command, name=canonical, aliases=[alternate])

    aliases: list[str] | None = getattr(group, "_odcli_aliases", None)
    if aliases is None:
        aliases = []
        setattr(group, "_odcli_aliases", aliases)
        original_shell_complete = group.shell_complete

        def shell_complete(ctx: click.Context, incomplete: str) -> list[CompletionItem[str]]:
            completions = original_shell_complete(ctx, incomplete)
            values = {item.value for item in completions}
            for alias_name in aliases:
                if alias_name.startswith(incomplete) and alias_name not in values:
                    alias_command = group.commands[alias_name]
                    if alias_command is not None and not alias_command.hidden:
                        completions.append(
                            CompletionItem(alias_name, help=alias_command.get_short_help_str())
                        )
            return completions

        setattr(group, "shell_complete", shell_complete)
    aliases.append(alternate)


# Domain command groups register through stable seams owned by their modules.
# Keep these calls at the composition boundary so later packages need not edit
# this registry's individual leaf callbacks.
register_module_commands(cli)
register_translation_commands(cli)
for _group, _canonical, _alternate in (
    ("env", "create", "checkout"),
    ("env", "ls", "list"),
    ("env", "rm", "remove"),
    ("backup", "ls", "list"),
    ("backup", "inspect", "show"),
    ("backup", "rm", "delete"),
    ("db", "ls", "list"),
    ("db", "rm", "drop"),
    ("postgres", "ps", "status"),
    ("resource", "ls", "list"),
    ("module", "ls", "list"),
):
    _register_short_alias(_group, _canonical, _alternate)


@cli.command("monitor")
@click.option("--headless", is_flag=True, default=False, help="Serve API only, no UI/browser.")
@click.option(
    "--host", default="127.0.0.1", help="Loopback bind address (127.0.0.1, localhost, or ::1)."
)
@click.option(
    "--port", type=int, default=None, help="Exact port (else auto-select 8069 or 8100-8120)."
)
@click.option("--no-open", is_flag=True, default=False, help="Do not open a browser.")
@click.pass_context
def monitor_cmd(
    ctx: click.Context, headless: bool, host: str, port: int | None, no_open: bool
) -> None:
    """Start the observability monitor (FastAPI + React UI)."""
    from odoo_instance_sdk.internal.serve import run_server

    # run_server raises SystemExit with an actionable hint if the dashboard
    # extra (fastapi/uvicorn) is missing; that propagates as exit 1.
    run_server(host=host, port=port, headless=headless, no_open=no_open)


def _resolve_odoo_bin(
    option_state: _OptionState,
    no_input: bool,
    output_mode: OutputMode,
    dry_run: bool,
    provenance: dict[str, list[str]],
) -> None:
    if option_state.odoo_bin is None:
        if no_input or output_mode is not OutputMode.RICH or dry_run:
            fail(output_mode, "init", "Missing required option --odoo-bin", dry_run=dry_run)
        option_state.odoo_bin = Path(click.prompt("Path to odoo-bin"))
        provenance["discovery"].append("odoo_bin")
    if not option_state.odoo_bin:
        fail(output_mode, "init", "odoo_bin is required", dry_run=dry_run)


if __name__ == "__main__":
    cli()
