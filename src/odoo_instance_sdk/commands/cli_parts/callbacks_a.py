from __future__ import annotations

import sys
from collections.abc import Hashable, Mapping
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from click.shell_completion import CompletionItem
from rich.console import Console
from rich.table import Table

from odoo_instance_sdk.commands.cli_parts.registration import (
    _generated_config_needs_repair,
    _OptionState,
    _register_initialized_project,
    _run_shell_command,
    _RunCommand,
    _ShellCommandFailure,
    _validate_generated_config_target,
    _write_project_generated_config,
    cli,
)
from odoo_instance_sdk.commands.context import CliContext, ResolvedContext, pass_cli_context
from odoo_instance_sdk.commands.module import register_module_commands
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    action_command,
    command_options,
    emit,
    emit_json_envelope,
    fail,
    failure_document,
    output_options,
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
from odoo_instance_sdk.commands.test import (
    resolve_module_test_selection,  # noqa: F401 - extracted module callback seam
)
from odoo_instance_sdk.commands.translations import register_translation_commands
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    LogfileAccessError,
)
from odoo_instance_sdk.internal.automation import (
    export_translations_command,  # noqa: F401 - extracted translation callback seam
    list_modules_command,  # noqa: F401 - extracted module callback seam
    update_modules_command,  # noqa: F401 - extracted module callback seam
)
from odoo_instance_sdk.internal.cli_format import rich_cell
from odoo_instance_sdk.internal.generated_config import (
    project_generated_config_path,
)
from odoo_instance_sdk.internal.vscode_generate import (
    launch_json,
    write_launch_json,
)
from odoo_instance_sdk.models import (
    DepsVerifyResult,
    PostgresClusterState,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.resources.testing import module_tests_command  # noqa: F401


def _ready_instance(ctx: CliContext) -> ResolvedContext:
    import odoo_instance_sdk.cli as _cli_shim

    return cast("ResolvedContext", _cli_shim.cli_context.ready_instance(ctx))


if TYPE_CHECKING:
    from collections.abc import Callable as TypeCallback

    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.commands.context import ResolvedContext
    from odoo_instance_sdk.execution import JsonValue
    from odoo_instance_sdk.internal.doctor import DoctorReport
    from odoo_instance_sdk.models import ClusterSnapshot
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    type CliLazyExport = (
        type[OdooClient | PostgresCluster | DoctorReport]
        | TypeCallback[[OdooClient, Path | None], DoctorReport]
        | TypeCallback[[PostgresCluster, PostgresClusterState], ClusterSnapshot]
        | TypeCallback[[ClusterSnapshot], int]
        | TypeCallback[[ClusterSnapshot], None]
    )

    class _DoctorRunner(Protocol):
        def __call__(
            self,
            client: OdooClient,
            project_path: Path | None,
            *,
            resolved_context: ResolvedContext | None = None,
        ) -> DoctorReport: ...


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
    if _manifest_dict(existing_cfg) == _manifest_dict(config):
        target = project_generated_config_path(resolved_project)
        repair = _generated_config_needs_repair(resolved_project, existing_cfg)
        if repair:
            if dry_run:
                result: JsonObject = {
                    **_manifest_dict(config),
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
                _validate_generated_config_target(target)
            except InstanceConfigurationError as exc:
                fail(output_mode, "init", str(exc), dry_run=dry_run)
            _write_project_generated_config(resolved_project, existing_cfg)
            _register_initialized_project(resolved_project)
            if output_mode is not OutputMode.RICH:
                emit_json_envelope(
                    ok=True,
                    command="init",
                    result={
                        **_manifest_dict(config),
                        "generated_config": {"path": str(target), "repaired": True},
                    },
                    provenance={},
                    mode=output_mode,
                )
            else:
                rich_print(f"Repaired generated config: {target}; manifest unchanged.")
            return True
        if not dry_run:
            _register_initialized_project(resolved_project)
        if output_mode is not OutputMode.RICH:
            emit_json_envelope(
                ok=True,
                command="init",
                result=_manifest_dict(config),
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


def _manifest_dict(
    config: ProjectConfig, *, postgres_allocated: bool = False
) -> dict[str, JsonValue]:
    postgres: dict[str, JsonValue] | None = None
    if config.postgres is not None:
        postgres = {
            "mode": config.postgres.mode,
            "image": config.postgres.image,
            "port": config.postgres.port,
            "user": config.postgres.user,
            "allocated_port": postgres_allocated,
        }
    test_instance: dict[str, JsonValue] | None = None
    if config.test_instance is not None:
        test_instance = {
            "base_url": config.test_instance.base_url,
            "database": config.test_instance.database,
            "git_branch": config.test_instance.git_branch,
        }
    return {
        "odoo_bin": str(config.odoo_bin) if config.odoo_bin else None,
        "python": str(config.python) if config.python else None,
        "source_config": str(config.source_config) if config.source_config else None,
        "default_source_database": config.default_source_database,
        "default_base_ref": config.default_base_ref,
        "ticket_link_enabled": config.ticket_link_enabled is True,
        "ticket_base_url": config.ticket_base_url,
        "refresh_after_hours": config.refresh_after_hours,
        "test_instance": test_instance,
        "preferred_http_port": config.preferred_http_port,
        "requirements": list(config.requirements),
        "default_run_args": list(config.default_run_args),
        "runtime_cwd": str(config.runtime_cwd) if config.runtime_cwd else None,
        "postgres": postgres,
    }


@cli.command(help="Diagnose project, runtime, and PostgreSQL.")
@output_options
@pass_cli_context
def doctor(ctx: CliContext, output_format: str | None, json_output: bool) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        import odoo_instance_sdk.cli as _cli_shim

        resolved = _cli_shim.cli_context._ready_instance_for_doctor(ctx)
        report = _cli_shim._run_doctor()(
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
    current_env: str | None = None
    for c in report.checks:
        if c.environment_id and c.environment_id != current_env:
            current_env = c.environment_id
            rich_print("")
            rich_print(f"[{current_env}] {c.environment_name or ''}")
        marker = {"ok": "OK", "warn": "WARN", "error": "ERROR", "info": "INFO"}.get(
            c.status, c.status
        )
        rich_print(f"  {marker:<5} {c.name}: {sanitize_diagnostic(c.detail)}")
        if c.facts:
            configured = c.facts.get("configured")
            available = c.facts.get("available")
            rich_print(f"    configured={configured} available={available}")
        for remediation in c.remediations:
            rich_print(
                "    remediation: "
                f"{sanitize_diagnostic(remediation.description)} "
                f"argv={list(remediation.argv)!r} "
                f"mutating={remediation.mutating} "
                f"dry_run_supported={remediation.dry_run_supported}"
            )
    for drift in report.drift:
        rich_print("")
        rich_print(f"[{drift.environment_id}] {drift.environment_name} drift")
        for component in drift.components:
            marker = component.status.upper()
            rich_print(
                f"  {marker:<8} {component.component}: "
                f"{sanitize_diagnostic(component.reason)} "
                f"({sanitize_diagnostic(component.remediation)})"
            )
        context = drift.git_context
        rich_print(
            "  GIT      context: "
            f"dirty={context.get('dirty')} ahead={context.get('ahead')} "
            f"behind={context.get('behind')}"
        )


@cli.command(help="Stop the selected environment's proven-owned runtime.")
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
        environment = runtime_context.require_environment()
        command = runtime_context.instance.stop_environment_command()
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
            result=lambda value: cast("JsonObject", value or {}),
            context={
                "environment_id": str(environment.id),
                "worktree_path": environment.worktree_path,
            },
            provenance=cast("JsonObject", runtime_context.output_provenance),
            rich=lambda _document: f"Stopped environment {environment.name} ({environment.id})",
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
    short_help="Start resolved Odoo in the foreground.",
)
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@command_options
@pass_cli_context
def run(
    ctx: CliContext,
    odoo_args: tuple[str, ...],
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_command_options(output_format, json_output, dry_run, command="run")
    try:
        runtime_context = _ready_instance(ctx)
        # Preview must retain the captured plan even when this read-only
        # precondition fails; normal execution keeps the early diagnostic
        # compatibility path in addition to the command-boundary recheck.
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
        command = runtime_context.instance.run_foreground_command(args=odoo_args)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "run", e, dry_run=dry_run)
    if not dry_run and runtime_context.is_environment:
        runtime_context.client.environments.record_use(runtime_context.require_environment())
    try:
        _status, value = run_or_preview(
            lambda: command,
            command_name="run",
            mode=output_mode,
            dry_run=dry_run,
            emit_normal=False,
        )
    except KeyboardInterrupt:
        value = 130
    except Exception as e:
        fail(output_mode, "run", e, dry_run=dry_run)
    if not dry_run:
        sys.exit(int(value or 0))
    return


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
        import odoo_instance_sdk.cli as _cli_shim

        runtime_context = _ready_instance(ctx)
        instance = runtime_context.instance
        status = _run_shell_command(
            command_name="eval",
            build_command=lambda: _cli_shim.eval_expression_command(
                instance, expression, commit=commit
            ),
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
        import odoo_instance_sdk.cli as _cli_shim

        runtime_context = _ready_instance(ctx)
        instance = runtime_context.instance
        status = _run_shell_command(
            command_name="exec",
            build_command=lambda: _cli_shim.exec_script_command(
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
        import odoo_instance_sdk.cli as _cli_shim

        status, _result = run_or_preview(
            lambda: _cli_shim.verify_deps_command(
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
    if document.ok:
        return "pip check: ok"
    details = document.error.details if document.error is not None else None
    if not isinstance(details, dict):
        return "pip check: issues"
    lines = ["pip check: issues"]
    for item in cast("list[JsonValue]", details.get("distributions", [])):
        if isinstance(item, dict) and isinstance(item.get("detail"), str):
            lines.append(f"distribution: {item['detail']}")
    for item in cast("list[JsonValue]", details.get("missing_imports", [])):
        if isinstance(item, dict):
            lines.append(f"missing import: {item.get('module')} ({item.get('import')})")
    return "\n".join(lines)


@cli.group("vscode", help="Generate VS Code launch configuration.")
def vscode_group() -> None:
    pass


def _rich_vscode_generate(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    table = Table("Field", "Value", title="VS Code launch")
    table.columns[1].overflow = "fold"
    if "written" in result:
        table.add_row("Output", rich_cell(result["written"]))
    profile = result.get("profile")
    if isinstance(profile, dict):
        table.add_row("Name", rich_cell(profile.get("name", "default")))
        table.add_row("Program", rich_cell(profile.get("program", "odoo")))
    if not table.rows:
        table.add_row("Status", "ready")
    output = StringIO()
    console = Console(file=output, color_system=None, width=9999)
    console.print(table)
    return output.getvalue().rstrip()


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
        import odoo_instance_sdk.cli as _cli_shim

        runtime_context = _ready_instance(ctx)
        runtime = runtime_context.runtime

        def operation() -> dict[str, JsonValue]:
            profile = _cli_shim.build_launch_profile(runtime)
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
