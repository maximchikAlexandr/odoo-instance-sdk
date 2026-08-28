from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click
import msgspec

from odoo_instance_sdk.commands import context as cli_context
from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.db import db_group
from odoo_instance_sdk.commands.env import env_group
from odoo_instance_sdk.commands.output import (
    OutputMode,
    emit_json_envelope,
    fail,
    output_options,
    resolve_output_mode,
    rich_print,
    sanitize_diagnostic,
)
from odoo_instance_sdk.commands.test import (
    project_execution_result,
    resolve_module_test_selection,
    run_module_tests,
    test_command,
)
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import (
    InstanceConfigurationError,
    LogfileAccessError,
    VscodeImportError,
)
from odoo_instance_sdk.internal.automation import (
    eval_expression,
    exec_script,
    export_translations,
    list_modules,
    plan_module_update,
    update_modules,
    verify_deps,
)
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_manifest import manifest_path, write_manifest
from odoo_instance_sdk.internal.vscode_generate import (
    build_launch_profile,
    launch_json,
    write_launch_json,
)
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.models import OdooTestSpec, PostgresClusterState, StartConfig
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.doctor import DoctorReport
    from odoo_instance_sdk.resources.postgres import PostgresCluster


def __getattr__(name: str) -> Any:
    """Resolve operation-only imports when a command callback actually needs them."""
    if name == "OdooClient":
        from odoo_instance_sdk.client import OdooClient

        globals()[name] = OdooClient
        return OdooClient
    if name in {"DoctorReport", "run_doctor"}:
        from odoo_instance_sdk.internal import doctor

        value = getattr(doctor, name)
        globals()[name] = value
        return value
    if name in {
        "cluster_snapshot",
        "emit_postgres_result",
        "print_status",
        "run_postgres_command",
        "status_exit_code",
    }:
        from odoo_instance_sdk.internal import postgres_cli

        value = getattr(postgres_cli, name)
        globals()[name] = value
        return value
    if name == "PostgresCluster":
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        globals()[name] = PostgresCluster
        return PostgresCluster
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _client_class() -> Any:
    return getattr(sys.modules[__name__], "OdooClient")


def _run_doctor() -> Any:
    return getattr(sys.modules[__name__], "run_doctor")


def _postgres_operation(name: str) -> Any:
    return getattr(sys.modules[__name__], name)


@click.group()
@click.version_option(package_name="odoo-instance-sdk")
@click.option(
    "--project",
    "project",
    type=click.Path(exists=False),
    default=None,
    help="Explicit project path.",
)
@click.option("--env", "env_selector", default=None, help="Environment selector (UUID or name).")
@click.pass_context
def cli(ctx: click.Context, project: str | None, env_selector: str | None) -> None:
    ctx.obj = CliContext(project=project, env=env_selector)


cli.add_command(env_group, name="env")
cli.add_command(test_command, name="test")
cli.add_command(db_group, name="db")


@cli.command()
@click.option("--odoo-bin", "odoo_bin", type=click.Path(), default=None, help="Path to odoo-bin.")
@click.option("--python", "python", default=None, help="Python interpreter or uv selector.")
@click.option(
    "--config", "source_config", type=click.Path(), default=None, help="Source odoo.conf path."
)
@click.option(
    "--database", "default_source_database", default=None, help="Default source database name."
)
@click.option(
    "--http-port", "preferred_http_port", type=int, default=None, help="Preferred HTTP port."
)
@click.option("--requirements", "requirements", multiple=True, help="Requirements files.")
@click.option("--run-arg", "run_args", multiple=True, help="Default run args.")
@click.option("--runtime-cwd", "runtime_cwd", type=click.Path(), default=None, help="Runtime cwd.")
@click.option(
    "--from-vscode",
    "from_vscode",
    type=click.Path(exists=False),
    default=None,
    help="Import from VS Code launch.json.",
)
@click.option("--launch-name", "launch_name", default=None, help="VS Code launch profile name.")
@click.option(
    "--postgres",
    "postgres_mode",
    type=click.Choice(["external", "compose"], case_sensitive=False),
    default="external",
    help="PostgreSQL cluster mode (external: reuse source cluster; compose: SDK-owned).",
)
@click.option(
    "--postgres-image",
    "postgres_image",
    default=None,
    help="Compose only; required with --no-input.",
)
@click.option(
    "--postgres-port",
    "postgres_port",
    type=int,
    default=None,
    help="Compose only; omitted = allocate free loopback port.",
)
@click.option(
    "--postgres-user",
    "postgres_user",
    default=None,
    help="Compose only; default: source db_user or 'odoo'.",
)
@click.option("--no-input", "no_input", is_flag=True, default=False, help="Forbid prompts.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Do not write.")
@output_options
@click.option(
    "--project", "project_path", type=click.Path(exists=False), default=None, help="Project path."
)
def init(
    odoo_bin: str | None,
    python: str | None,
    source_config: str | None,
    default_source_database: str | None,
    preferred_http_port: int | None,
    requirements: tuple[str, ...],
    run_args: tuple[str, ...],
    runtime_cwd: str | None,
    from_vscode: str | None,
    launch_name: str | None,
    postgres_mode: str,
    postgres_image: str | None,
    postgres_port: int | None,
    postgres_user: str | None,
    no_input: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
    project_path: str | None,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    resolved_project = Path(project_path) if project_path is not None else Path.cwd()
    provenance: dict[str, list[str]] = {"option": [], "vscode": [], "discovery": [], "default": []}

    option_state = _OptionState(
        odoo_bin=Path(odoo_bin) if odoo_bin else None,
        python=python,
        source_config=Path(source_config) if source_config else None,
        default_source_database=default_source_database,
        preferred_http_port=preferred_http_port,
        requirements=tuple(requirements),
        default_run_args=tuple(run_args),
        runtime_cwd=Path(runtime_cwd) if runtime_cwd else None,
    )
    _record_option_provenance(option_state, provenance)

    if from_vscode is not None:
        vscode_cfg = _import_vscode(from_vscode, launch_name, no_input, output_mode)
        if vscode_cfg is None:
            return
        _merge_vscode(option_state, vscode_cfg, provenance)

    _resolve_odoo_bin(option_state, no_input, output_mode, dry_run, provenance)

    postgres_cfg, postgres_allocated = _resolve_postgres_state(
        postgres_mode=postgres_mode,
        postgres_image=postgres_image,
        postgres_port=postgres_port,
        postgres_user=postgres_user,
        source_config=option_state.source_config,
        no_input=no_input,
        output_mode=output_mode,
        project_path=resolved_project,
    )
    if postgres_cfg is not None:
        provenance["option"].append("postgres")

    config = ProjectConfig(
        repository_root=resolved_project.resolve(),
        odoo_bin=option_state.odoo_bin,
        python=option_state.python,
        source_config=option_state.source_config,
        default_source_database=option_state.default_source_database,
        preferred_http_port=option_state.preferred_http_port,
        requirements=option_state.requirements,
        default_run_args=option_state.default_run_args,
        runtime_cwd=option_state.runtime_cwd,
        postgres=postgres_cfg,
    )

    existing = manifest_path(resolved_project)
    if existing.is_file() and _handle_existing_manifest(
        existing, resolved_project, config, no_input, output_mode
    ):
        return

    if dry_run:
        if json_output:
            emit_json_envelope(
                ok=True,
                command="init",
                result=_manifest_dict(config, postgres_allocated=postgres_allocated),
                provenance=provenance,
                dry_run=True,
                mode=output_mode,
            )
        else:
            rich_print("Dry run — no files written.")
            rich_print(config.to_manifest(), preserve_newlines=True)
        return

    write_manifest(resolved_project, config)
    if json_output:
        emit_json_envelope(
            ok=True,
            command="init",
            result=_manifest_dict(config, postgres_allocated=postgres_allocated),
            provenance=provenance,
            dry_run=False,
            mode=output_mode,
        )
    else:
        rich_print(f"Wrote {existing}")


def _resolve_postgres_state(
    *,
    postgres_mode: str,
    postgres_image: str | None,
    postgres_port: int | None,
    postgres_user: str | None,
    source_config: Path | None,
    no_input: bool,
    output_mode: OutputMode,
    project_path: Path,
) -> tuple[PostgresProjectConfig | None, bool]:
    mode = "compose" if postgres_mode.lower() == "compose" else "external"
    if mode == "external":
        return None, False

    if postgres_image is None:
        if no_input or output_mode is not OutputMode.RICH:
            fail(output_mode, "init", "Missing required option --postgres-image for compose mode")
        postgres_image = click.prompt("PostgreSQL image (e.g. pgvector/pgvector:pg16)")

    allocated = False
    if postgres_port is None:
        postgres_port = find_free_port(
            "postgres", _open_catalog_optional(), exclude_project=project_path
        )
        allocated = True

    if postgres_user is None:
        postgres_user = _default_postgres_user(source_config)

    cfg = PostgresProjectConfig(
        mode="compose",
        image=postgres_image,
        port=postgres_port,
        user=postgres_user,
    )
    return cfg, allocated


def _open_catalog_optional() -> Any:
    """Open the catalog read-only; return None if missing/unreadable."""
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog_path = get_catalog_path()
    if not catalog_path.is_file():
        return None
    try:
        return BackupCatalog(db_path=catalog_path)
    except Exception:
        return None


def _default_postgres_user(source_config: Path | None) -> str:
    if source_config is not None and source_config.is_file():
        try:
            start_cfg = StartConfig.from_odoo_config(source_config)
            if start_cfg.db_user:
                return start_cfg.db_user
        except Exception:
            pass
    return "odoo"


@dataclass(slots=True)
class _OptionState:
    odoo_bin: Path | None = None
    python: str | Path | None = None
    source_config: Path | None = None
    default_source_database: str | None = None
    preferred_http_port: int | None = None
    requirements: tuple[str, ...] = ()
    default_run_args: tuple[str, ...] = ()
    runtime_cwd: Path | None = None


def _record_option_provenance(state: _OptionState, provenance: dict[str, list[str]]) -> None:
    if state.odoo_bin is not None:
        provenance["option"].append("odoo_bin")
    if state.python is not None:
        provenance["option"].append("python")
    if state.source_config is not None:
        provenance["option"].append("source_config")
    if state.default_source_database is not None:
        provenance["option"].append("default_source_database")
    if state.preferred_http_port is not None:
        provenance["option"].append("preferred_http_port")
    if state.requirements:
        provenance["option"].append("requirements")
    if state.default_run_args:
        provenance["option"].append("default_run_args")
    if state.runtime_cwd is not None:
        provenance["option"].append("runtime_cwd")


def _import_vscode(
    from_vscode: str, launch_name: str | None, no_input: bool, output_mode: OutputMode
) -> ProjectConfig | None:
    try:
        result = import_vscode_launch(from_vscode, launch_name=launch_name, no_input=no_input)
    except VscodeImportError as e:
        fail(output_mode, "init", str(e))
    return result.config


def _merge_vscode(
    state: _OptionState, vscode_cfg: ProjectConfig, provenance: dict[str, list[str]]
) -> None:
    provenance["vscode"].append("imported")
    if state.odoo_bin is None and vscode_cfg.odoo_bin is not None:
        state.odoo_bin = vscode_cfg.odoo_bin
    if state.python is None and vscode_cfg.python is not None:
        state.python = vscode_cfg.python
    if state.source_config is None and vscode_cfg.source_config is not None:
        state.source_config = vscode_cfg.source_config
    if state.default_source_database is None and vscode_cfg.default_source_database is not None:
        state.default_source_database = vscode_cfg.default_source_database
    if state.preferred_http_port is None and vscode_cfg.preferred_http_port is not None:
        state.preferred_http_port = vscode_cfg.preferred_http_port
    if not state.default_run_args and vscode_cfg.default_run_args:
        state.default_run_args = vscode_cfg.default_run_args
    if state.runtime_cwd is None and vscode_cfg.runtime_cwd is not None:
        state.runtime_cwd = vscode_cfg.runtime_cwd


def _handle_existing_manifest(
    existing: Path,
    resolved_project: Path,
    config: ProjectConfig,
    no_input: bool,
    output_mode: OutputMode,
) -> bool:
    try:
        existing_cfg = ProjectConfig.load(resolved_project)
    except Exception as e:
        fail(output_mode, "init", f"Existing manifest unreadable: {e}")
    # Comparison excludes ``postgres_allocated`` (dry-run-only flag); both
    # sides default to False here.
    if _manifest_dict(existing_cfg) == _manifest_dict(config):
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
        fail(output_mode, "init", "manifest exists and differs; remove it first or adjust options")
    if not click.confirm("Manifest exists and differs; overwrite?", default=False):
        rich_print("Aborted.")
        return True
    return False


def _manifest_dict(config: ProjectConfig, *, postgres_allocated: bool = False) -> dict[str, Any]:
    postgres: dict[str, Any] | None = None
    if config.postgres is not None:
        postgres = {
            "mode": config.postgres.mode,
            "image": config.postgres.image,
            "port": config.postgres.port,
            "user": config.postgres.user,
            "allocated_port": postgres_allocated,
        }
    test_instance: dict[str, Any] | None = None
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
        "refresh_after_hours": config.refresh_after_hours,
        "test_instance": test_instance,
        "preferred_http_port": config.preferred_http_port,
        "requirements": list(config.requirements),
        "default_run_args": list(config.default_run_args),
        "runtime_cwd": str(config.runtime_cwd) if config.runtime_cwd else None,
        "postgres": postgres,
    }


@cli.command()
@output_options
@pass_cli_context
def doctor(ctx: CliContext, output_format: str | None, json_output: bool) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        project_path = cli_context.resolve_project_path(ctx)
        client = _client_class()(config=OdooClientConfig(executable="odoo"))
        report = _run_doctor()(client, project_path if project_path != Path.cwd() else None)
    except Exception as e:
        fail(output_mode, "doctor", str(e))
    if json_output:
        emit_json_envelope(
            ok=report.ok,
            command="doctor",
            context=report.context,
            result={
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status,
                        "detail": sanitize_diagnostic(c.detail),
                        "environment_id": c.environment_id,
                        "environment_name": c.environment_name,
                    }
                    for c in report.checks
                ]
            },
            error_code="doctor_failed" if not report.ok else None,
            error_message="doctor reported failed checks" if not report.ok else None,
            mode=output_mode,
        )
    else:
        _print_doctor(report)
    sys.exit(0 if report.ok else 1)


def _print_doctor(report: object) -> None:
    rep = cast("DoctorReport", report)
    current_env: str | None = None
    for c in rep.checks:
        if c.environment_id and c.environment_id != current_env:
            current_env = c.environment_id
            rich_print("")
            rich_print(f"[{current_env}] {c.environment_name or ''}")
        marker = {"ok": "OK", "warn": "WARN", "error": "ERROR", "info": "INFO"}.get(
            c.status, c.status
        )
        rich_print(f"  {marker:<5} {c.name}: {sanitize_diagnostic(c.detail)}")


@cli.command()
@pass_cli_context
def run(ctx: CliContext) -> None:
    try:
        client, env_obj, instance = cli_context.ready_instance(ctx)
        if not cli_context._check_port_free(env_obj):
            click.echo(
                f"port-conflict: {env_obj.http_interface}:{env_obj.http_port} is occupied "
                "(ownership unknown)",
                err=True,
            )
            sys.exit(1)
        client.environments.record_use(env_obj)
    except SystemExit:
        raise
    except Exception as e:
        fail(False, "run", str(e))
    try:
        exit_code = instance.run_foreground()
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        fail(False, "run", str(e))
    sys.exit(exit_code)


@cli.command()
@click.option("-n", "--tail", type=click.IntRange(min=1), default=100, show_default=True)
@click.option("-f", "--follow", is_flag=True, default=False)
@pass_cli_context
def logs(ctx: CliContext, tail: int, follow: bool) -> None:
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        for line in instance.iter_logs(tail=tail, follow=follow):
            sys.stdout.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        sys.exit(130)
    except LogfileAccessError as e:
        fail(False, "logs", str(e))
    except InstanceConfigurationError as e:
        fail(False, "logs", str(e))
    except Exception as e:
        fail(False, "logs", str(e))


@cli.command()
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@pass_cli_context
def shell(ctx: CliContext, odoo_args: tuple[str, ...]) -> None:
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
    except SystemExit:
        raise
    except Exception as e:
        fail(False, "shell", str(e))
    try:
        exit_code = instance.shell(args=list(odoo_args))
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        fail(False, "shell", str(e))
    sys.exit(exit_code)


@cli.command("eval")
@click.argument("expression")
@click.option(
    "--commit", "commit", is_flag=True, default=False, help="Commit after eval (best-effort)."
)
@output_options
@pass_cli_context
def eval_cmd(
    ctx: CliContext,
    expression: str,
    commit: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        outcome = eval_expression(instance, expression, commit=commit)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "eval", str(e))
    if outcome.returncode != 0:
        fail(output_mode, "eval", f"shell exited {outcome.returncode}: {outcome.stderr.strip()}")
    result = outcome.payload.get("result") if outcome.payload else None
    if json_output:
        emit_json_envelope(
            ok=True,
            command="eval",
            result={"result": result, "commit": commit},
            mode=output_mode,
        )
    else:
        rich_print(json.dumps(result, default=str, indent=2), preserve_newlines=True)
    sys.exit(0)


@cli.command("exec")
@click.argument("script")
@click.argument("script_args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--commit", "commit", is_flag=True, default=False, help="Commit after exec (best-effort)."
)
@output_options
@pass_cli_context
def exec_cmd(
    ctx: CliContext,
    script: str,
    script_args: tuple[str, ...],
    commit: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    if script == "-":
        source = sys.stdin.read()
    else:
        p = Path(script)
        if not p.is_file():
            fail(output_mode, "exec", f"script not found: {script}")
        try:
            source = p.read_text(encoding="utf-8")
        except OSError as e:
            fail(output_mode, "exec", f"cannot read script: {e}")
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        outcome = exec_script(instance, source, argv=tuple(script_args), commit=commit)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "exec", str(e))
    if json_output:
        emit_json_envelope(
            ok=True,
            command="exec",
            result={
                "returncode": outcome.returncode,
                "stdout": outcome.stdout,
                "stderr": sanitize_diagnostic(outcome.stderr) if outcome.stderr else "",
                "commit": commit,
            },
            mode=output_mode,
        )
    else:
        rich_print(outcome.stdout, end="", preserve_newlines=True)
        if outcome.stderr:
            click.echo(sanitize_diagnostic(outcome.stderr), err=True, nl=False)
    sys.exit(outcome.returncode)


@cli.group("module")
def module_group() -> None:
    pass


@module_group.command("list")
@click.argument("modules", nargs=-1)
@click.option("--state", "state", default=None, help="Filter by state.")
@output_options
@pass_cli_context
def module_list(
    ctx: CliContext,
    modules: tuple[str, ...],
    state: str | None,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        records = list_modules(instance, names=tuple(modules), state=state)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.list", str(e))
    if json_output:
        emit_json_envelope(
            ok=True,
            command="module.list",
            result={"modules": [r.to_dict() for r in records]},
            mode=output_mode,
        )
    else:
        rich_print(f"{'NAME':<30} {'STATE':<15} {'VERSION'}")
        for r in records:
            rich_print(
                f"{r.name:<30} {r.state:<15} {r.installed_version or r.latest_version or ''}"
            )
    sys.exit(0)


@module_group.command("update")
@click.argument("modules", nargs=-1, required=True)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Confirm execution.")
@output_options
@pass_cli_context
def module_update(
    ctx: CliContext,
    modules: tuple[str, ...],
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        _client, env_obj, instance = cli_context.ready_instance(ctx)
        plan = plan_module_update(instance, tuple(modules))
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.update", str(e))
    if plan.not_installed:
        fail(
            output_mode,
            "module.update",
            f"modules not installed: {', '.join(plan.not_installed)}",
        )
    if dry_run:
        if json_output:
            emit_json_envelope(
                ok=True,
                command="module.update",
                result={"modules": plan.modules, "dry_run": True},
                mode=output_mode,
            )
        else:
            rich_print("Dry run — modules to update:")
            for m in plan.modules:
                rich_print(f"  {m}")
        sys.exit(0)
        return
    if not yes:
        fail(output_mode, "module.update", "module update requires --yes")
    _module_update_execute(instance, plan.modules, env_obj, output_mode=output_mode)


def _module_update_execute(
    instance: Any,
    modules: list[str],
    env_obj: Any,
    *,
    output_mode: OutputMode,
) -> None:
    try:
        outcome = update_modules(instance, tuple(modules), env_id=str(env_obj.id))
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.update", str(e))
    if outcome.returncode != 0:
        fail(
            output_mode,
            "module.update",
            f"shell exited {outcome.returncode}: {outcome.stderr.strip()}",
        )
    updated = outcome.payload.get("result", {}).get("updated", []) if outcome.payload else []
    if output_mode is not OutputMode.RICH:
        emit_json_envelope(
            ok=True,
            command="module.update",
            result={"updated": updated, "dry_run": False},
            mode=output_mode,
        )
    else:
        rich_print("Updated modules:")
        for m in updated:
            rich_print(f"  {m}")
    sys.exit(0)


@module_group.command("test")
@click.argument("modules", nargs=-1, required=True)
@click.option("--test-tags", "test_tags", required=True, help="Test tags.")
@click.option("--reload-tests", "reload_tests", is_flag=True, default=False)
@click.option("--allow-empty", "allow_empty", is_flag=True, default=False)
@output_options
@pass_cli_context
def module_test(
    ctx: CliContext,
    modules: tuple[str, ...],
    test_tags: str,
    reload_tests: bool,
    allow_empty: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        _client, env_obj, instance = cli_context.ready_instance(ctx)
        start_config = instance.config.start_config
        if start_config is None:
            raise RuntimeError(  # noqa: TRY301
                "selected environment has no generated Odoo config"
            )
        selection = resolve_module_test_selection(
            env_obj.worktree_path,
            start_config,
            tuple(modules),
            test_tags,
        )
        spec = OdooTestSpec(
            modules=tuple(sorted(set(modules))),
            test_tags=test_tags,
            reload_tests=reload_tests,
            allow_empty=allow_empty,
        )
        typed, diagnostic = run_module_tests(
            instance,
            selection,
            spec,
            http_interface=env_obj.http_interface,
            http_port=env_obj.http_port,
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "module.test", str(e))
    result = project_execution_result(env_obj, selection, spec, typed)
    if json_output:
        emit_json_envelope(
            ok=True,
            command="module.test",
            result=result,
            mode=output_mode,
        )
    else:
        rich_print(f"environment={env_obj.name} ({env_obj.id})")
        rich_print("selection=module")
        rich_print(f"modules={', '.join(sorted(set(modules)))}")
        rich_print(
            f"tests={typed.counts['tests']} ok={typed.counts['successful']} "
            f"failed={typed.counts['failed']} errors={typed.counts['errors']} "
            f"skipped={typed.counts['skipped']}"
        )
        rich_print(f"exit_code={typed.exit_code}")
    if diagnostic:
        click.echo(sanitize_diagnostic(diagnostic), err=True)
    sys.exit(typed.exit_code)


@cli.group("translations")
def translations_group() -> None:
    pass


@translations_group.command("export")
@click.option("--module", "modules", multiple=True, required=True, help="Module name.")
@click.option("--language", "languages", multiple=True, required=True, help="Language code.")
@output_options
@pass_cli_context
def translations_export(
    ctx: CliContext,
    modules: tuple[str, ...],
    languages: tuple[str, ...],
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        _client, env_obj, instance = cli_context.ready_instance(ctx)
        results = export_translations(
            instance,
            tuple(modules),
            tuple(languages),
            worktree_root=Path(env_obj.worktree_path),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "translations.export", str(e))
    if json_output:
        emit_json_envelope(
            ok=True,
            command="translations.export",
            result={
                "exports": [
                    {
                        "module": r.module,
                        "requested_lang": r.requested_lang,
                        "actual_filename": r.actual_filename,
                        "path": str(r.path),
                        "bytes_written": r.bytes_written,
                    }
                    for r in results
                ]
            },
            mode=output_mode,
        )
    else:
        for r in results:
            rich_print(
                f"{r.module} {r.requested_lang} -> {r.actual_filename} "
                f"({r.bytes_written} bytes at {r.path})"
            )
    sys.exit(0)


@cli.group("deps")
def deps_group() -> None:
    pass


@deps_group.command("verify")
@output_options
@pass_cli_context
def deps_verify(ctx: CliContext, output_format: str | None, json_output: bool) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        _client, env_obj, _instance = cli_context.ready_instance(ctx)
        recorded_python = Path(env_obj.python_environment_path)
        if recorded_python.is_dir():
            recorded_python = recorded_python / "bin" / "python"
        result = verify_deps(
            recorded_python=recorded_python,
            worktree_root=Path(env_obj.worktree_path),
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "deps.verify", str(e))
    exit_code = 1 if result.missing_imports else 0
    if json_output:
        emit_json_envelope(
            ok=True,
            command="deps.verify",
            result={
                "distributions": result.distributions,
                "missing_imports": result.missing_imports,
                "pip_check_ok": result.pip_check_ok,
                "pip_check_output": result.pip_check_output,
            },
            mode=output_mode,
        )
    else:
        if result.pip_check_ok:
            rich_print("pip check: ok")
        else:
            rich_print("pip check: issues")
            for d in result.distributions:
                rich_print(f"  {d['detail']}")
        if result.missing_imports:
            rich_print("missing imports:")
            for m in result.missing_imports:
                rich_print(f"  {m['module']}: {m['import']}")
        else:
            rich_print("imports: ok")
    sys.exit(exit_code)


@cli.group("vscode")
def vscode_group() -> None:
    pass


@vscode_group.command("generate")
@click.option(
    "--write", "write_file", is_flag=True, default=False, help="Write .vscode/launch.json."
)
@output_options
@pass_cli_context
def vscode_generate(
    ctx: CliContext, write_file: bool, output_format: str | None, json_output: bool
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    try:
        client, env_obj, _instance = cli_context.ready_instance(ctx)
        profile = build_launch_profile(client, env_obj)
    except SystemExit:
        raise
    except Exception as e:
        fail(output_mode, "vscode.generate", str(e))
    if write_file:
        try:
            project_path = cli_context.resolve_project_path(ctx)
            content = launch_json(profile)
            written = write_launch_json(project_path, content)
        except Exception as e:
            fail(output_mode, "vscode.generate", str(e))
        if json_output:
            emit_json_envelope(
                ok=True,
                command="vscode.generate",
                result={"profile": profile, "written": str(written), "dry_run": False},
                mode=output_mode,
            )
        else:
            rich_print(f"Wrote {written}")
        sys.exit(0)
        return
    if json_output:
        emit_json_envelope(
            ok=True,
            command="vscode.generate",
            result={"profile": profile, "dry_run": True},
            mode=output_mode,
        )
    else:
        rich_print(launch_json(profile), end="", preserve_newlines=True)
    sys.exit(0)


@cli.group("postgres")
def postgres_group() -> None:
    """Project-level PostgreSQL cluster lifecycle (read-only / idempotent)."""


@postgres_group.command("approve-image")
@click.option("--image-digest", required=True, help="Exact OCI RepoDigest shown by Docker.")
@click.option(
    "--timeout",
    type=float,
    default=60.0,
    show_default=True,
    help="Seconds allowed for Docker pull and inspect.",
)
@output_options
@pass_cli_context
def postgres_approve_image(
    ctx: CliContext,
    image_digest: str,
    timeout: float,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    """Approve the current compose image in the local, non-repository trust store."""
    cluster, _ = _postgres_operation("run_postgres_command")(
        ctx,
        command="postgres.approve-image",
        output_mode=output_mode,
        operation=lambda candidate: candidate.approve_image(image_digest, timeout=timeout),
    )
    if json_output:
        emit_json_envelope(
            ok=True,
            command="postgres.approve-image",
            result={
                "approved": True,
                "image": cluster.to_diagnostic_dict()["image"],
                "digest": image_digest,
            },
            mode=output_mode,
        )
    else:
        rich_print(f"approved image={cluster.to_diagnostic_dict()['image']} digest={image_digest}")


@postgres_group.command("status")
@output_options
@pass_cli_context
def postgres_status(ctx: CliContext, output_format: str | None, json_output: bool) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    cluster, state = _postgres_operation("run_postgres_command")(
        ctx,
        command="postgres.status",
        output_mode=output_mode,
        operation=lambda candidate: candidate.status(),
    )
    snapshot = _postgres_operation("cluster_snapshot")(cluster, state)
    if json_output:
        emit_json_envelope(
            ok=True,
            command="postgres.status",
            result=msgspec.to_builtins(snapshot),
            mode=output_mode,
        )
    else:
        _postgres_operation("print_status")(snapshot)
    sys.exit(_postgres_operation("status_exit_code")(snapshot))


@postgres_group.command("up")
@click.option(
    "--wait-timeout",
    "wait_timeout",
    type=float,
    default=60.0,
    help="Seconds to wait for the cluster to become healthy.",
)
@output_options
@pass_cli_context
def postgres_up(
    ctx: CliContext, wait_timeout: float, output_format: str | None, json_output: bool
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH

    def ensure(candidate: PostgresCluster) -> PostgresClusterState:
        candidate.ensure_running(timeout=wait_timeout)
        return candidate.status()

    cluster, state = _postgres_operation("run_postgres_command")(
        ctx, command="postgres.up", output_mode=output_mode, operation=ensure
    )
    _postgres_operation("emit_postgres_result")(
        cluster=cluster, state=state, command="postgres.up", output_mode=output_mode
    )
    sys.exit(0)


@postgres_group.command("stop")
@click.option(
    "--timeout",
    "timeout",
    type=float,
    default=30.0,
    help="Seconds to wait for graceful stop.",
)
@output_options
@pass_cli_context
def postgres_stop(
    ctx: CliContext, timeout: float, output_format: str | None, json_output: bool
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH

    def stop(candidate: PostgresCluster) -> PostgresClusterState:
        candidate.stop(timeout=timeout)
        return candidate.status()

    cluster, state = _postgres_operation("run_postgres_command")(
        ctx, command="postgres.stop", output_mode=output_mode, operation=stop
    )
    _postgres_operation("emit_postgres_result")(
        cluster=cluster, state=state, command="postgres.stop", output_mode=output_mode
    )
    sys.exit(0)


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
            fail(output_mode, "init", "Missing required option --odoo-bin")
        option_state.odoo_bin = Path(click.prompt("Path to odoo-bin"))
        provenance["discovery"].append("odoo_bin")
    if not option_state.odoo_bin:
        fail(output_mode, "init", "odoo_bin is required")


if __name__ == "__main__":
    cli()
