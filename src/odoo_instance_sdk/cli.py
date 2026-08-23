from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import click

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import VscodeImportError
from odoo_instance_sdk.internal import context as cli_context
from odoo_instance_sdk.internal.automation import (
    eval_expression,
    exec_script,
    export_translations,
    list_modules,
    plan_module_update,
    run_module_tests,
    update_modules,
    verify_deps,
)
from odoo_instance_sdk.internal.cli_env import env_group
from odoo_instance_sdk.internal.cli_output import emit_json_envelope, fail, sanitize_diagnostic
from odoo_instance_sdk.internal.doctor import DoctorReport, run_doctor
from odoo_instance_sdk.internal.port_allocation import find_free_port
from odoo_instance_sdk.internal.project_manifest import manifest_path, write_manifest
from odoo_instance_sdk.internal.vscode_generate import (
    build_launch_profile,
    launch_json,
    write_launch_json,
)
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster


@click.group()
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
    ctx.ensure_object(dict)
    ctx.obj["project"] = project
    ctx.obj["env"] = env_selector


cli.add_command(env_group, name="env")


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
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
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
    json_output: bool,
    project_path: str | None,
) -> None:
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
        vscode_cfg = _import_vscode(from_vscode, launch_name, no_input, json_output)
        if vscode_cfg is None:
            return
        _merge_vscode(option_state, vscode_cfg, provenance)

    _resolve_odoo_bin(option_state, no_input, json_output, dry_run, provenance)

    postgres_cfg, postgres_allocated = _resolve_postgres_state(
        postgres_mode=postgres_mode,
        postgres_image=postgres_image,
        postgres_port=postgres_port,
        postgres_user=postgres_user,
        source_config=option_state.source_config,
        no_input=no_input,
        json_output=json_output,
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
        existing, resolved_project, config, no_input, json_output
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
            )
        else:
            click.echo("Dry run — no files written.")
            click.echo(config.to_manifest())
        return

    write_manifest(resolved_project, config)
    if json_output:
        emit_json_envelope(
            ok=True,
            command="init",
            result=_manifest_dict(config, postgres_allocated=postgres_allocated),
            provenance=provenance,
            dry_run=False,
        )
    else:
        click.echo(f"Wrote {existing}")


def _resolve_odoo_bin(
    option_state: _OptionState,
    no_input: bool,
    json_output: bool,
    dry_run: bool,
    provenance: dict[str, list[str]],
) -> None:
    if option_state.odoo_bin is None:
        if no_input or json_output or dry_run:
            fail(json_output, "init", "Missing required option --odoo-bin")
        option_state.odoo_bin = Path(click.prompt("Path to odoo-bin"))
        provenance["discovery"].append("odoo_bin")
    if not option_state.odoo_bin:
        fail(json_output, "init", "odoo_bin is required")


def _resolve_postgres_state(
    *,
    postgres_mode: str,
    postgres_image: str | None,
    postgres_port: int | None,
    postgres_user: str | None,
    source_config: Path | None,
    no_input: bool,
    json_output: bool,
    project_path: Path,
) -> tuple[PostgresProjectConfig | None, bool]:
    mode = "compose" if postgres_mode.lower() == "compose" else "external"
    if mode == "external":
        return None, False

    if postgres_image is None:
        if no_input or json_output:
            fail(json_output, "init", "Missing required option --postgres-image for compose mode")
        postgres_image = click.prompt("PostgreSQL image (e.g. pgvector/pgvector:pg16)")

    allocated = False
    if postgres_port is None:
        postgres_port = _allocate_free_loopback_port(project_path)
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


def _allocate_free_loopback_port(project_path: Path) -> int:
    """Allocate a free loopback port for postgres via cross-project check."""
    catalog = _open_catalog_optional()
    return find_free_port("postgres", catalog, exclude_project=project_path)


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
    from_vscode: str, launch_name: str | None, no_input: bool, json_output: bool
) -> ProjectConfig | None:
    try:
        result = import_vscode_launch(from_vscode, launch_name=launch_name, no_input=no_input)
    except VscodeImportError as e:
        fail(json_output, "init", str(e))
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
    json_output: bool,
) -> bool:
    try:
        existing_cfg = ProjectConfig.load(resolved_project)
    except Exception as e:
        fail(json_output, "init", f"Existing manifest unreadable: {e}")
    # Comparison excludes ``postgres_allocated`` (dry-run-only flag); both
    # sides default to False here.
    if _manifest_dict(existing_cfg) == _manifest_dict(config):
        if json_output:
            emit_json_envelope(
                ok=True, command="init", result=_manifest_dict(config), provenance={}, dry_run=True
            )
        else:
            click.echo("Manifest already up to date; no-op.")
        return True
    if no_input or json_output:
        fail(json_output, "init", "manifest exists and differs; remove it first or adjust options")
    if not click.confirm("Manifest exists and differs; overwrite?", default=False):
        click.echo("Aborted.")
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
    return {
        "odoo_bin": str(config.odoo_bin) if config.odoo_bin else None,
        "python": str(config.python) if config.python else None,
        "source_config": str(config.source_config) if config.source_config else None,
        "default_source_database": config.default_source_database,
        "preferred_http_port": config.preferred_http_port,
        "requirements": list(config.requirements),
        "default_run_args": list(config.default_run_args),
        "runtime_cwd": str(config.runtime_cwd) if config.runtime_cwd else None,
        "postgres": postgres,
    }


@cli.command()
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def doctor(ctx: click.Context, json_output: bool) -> None:
    project_path = cli_context.resolve_project_path(ctx)
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    try:
        report = run_doctor(client, project_path if project_path != Path.cwd() else None)
    except Exception as e:
        fail(json_output, "doctor", str(e))
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
            click.echo(f"\n[{current_env}] {c.environment_name or ''}")
        marker = {"ok": "OK", "warn": "WARN", "error": "ERROR", "info": "INFO"}.get(
            c.status, c.status
        )
        click.echo(f"  {marker:<5} {c.name}: {sanitize_diagnostic(c.detail)}")


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
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
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def shell(ctx: click.Context, odoo_args: tuple[str, ...]) -> None:
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
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def eval_cmd(ctx: click.Context, expression: str, commit: bool, json_output: bool) -> None:
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        outcome = eval_expression(instance, expression, commit=commit)
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "eval", str(e))
    if outcome.returncode != 0:
        fail(json_output, "eval", f"shell exited {outcome.returncode}: {outcome.stderr.strip()}")
    result = outcome.payload.get("result") if outcome.payload else None
    if json_output:
        emit_json_envelope(ok=True, command="eval", result={"result": result, "commit": commit})
    else:
        click.echo(json.dumps(result, default=str, indent=2))
    sys.exit(0)


@cli.command("exec")
@click.argument("script")
@click.argument("script_args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--commit", "commit", is_flag=True, default=False, help="Commit after exec (best-effort)."
)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def exec_cmd(
    ctx: click.Context,
    script: str,
    script_args: tuple[str, ...],
    commit: bool,
    json_output: bool,
) -> None:
    if script == "-":
        source = sys.stdin.read()
    else:
        p = Path(script)
        if not p.is_file():
            fail(json_output, "exec", f"script not found: {script}")
        try:
            source = p.read_text(encoding="utf-8")
        except OSError as e:
            fail(json_output, "exec", f"cannot read script: {e}")
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        outcome = exec_script(instance, source, argv=tuple(script_args), commit=commit)
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "exec", str(e))
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
        )
    else:
        click.echo(outcome.stdout, nl=False)
        if outcome.stderr:
            click.echo(sanitize_diagnostic(outcome.stderr), err=True, nl=False)
    sys.exit(outcome.returncode)


@cli.group("module")
def module_group() -> None:
    pass


@module_group.command("list")
@click.argument("modules", nargs=-1)
@click.option("--state", "state", default=None, help="Filter by state.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def module_list(
    ctx: click.Context, modules: tuple[str, ...], state: str | None, json_output: bool
) -> None:
    try:
        _client, _env, instance = cli_context.ready_instance(ctx)
        records = list_modules(instance, names=tuple(modules), state=state)
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "module.list", str(e))
    if json_output:
        emit_json_envelope(
            ok=True, command="module.list", result={"modules": [r.to_dict() for r in records]}
        )
    else:
        click.echo(f"{'NAME':<30} {'STATE':<15} {'VERSION'}")
        for r in records:
            click.echo(
                f"{r.name:<30} {r.state:<15} {r.installed_version or r.latest_version or ''}"
            )
    sys.exit(0)


@module_group.command("update")
@click.argument("modules", nargs=-1, required=True)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Confirm execution.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def module_update(
    ctx: click.Context,
    modules: tuple[str, ...],
    dry_run: bool,
    yes: bool,
    json_output: bool,
) -> None:
    try:
        _client, env_obj, instance = cli_context.ready_instance(ctx)
        plan = plan_module_update(instance, tuple(modules))
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "module.update", str(e))
    if plan.not_installed:
        fail(
            json_output,
            "module.update",
            f"modules not installed: {', '.join(plan.not_installed)}",
        )
    if dry_run:
        if json_output:
            emit_json_envelope(
                ok=True, command="module.update", result={"modules": plan.modules, "dry_run": True}
            )
        else:
            click.echo("Dry run — modules to update:")
            for m in plan.modules:
                click.echo(f"  {m}")
        sys.exit(0)
        return
    if not yes:
        fail(json_output, "module.update", "module update requires --yes")
    _module_update_execute(instance, plan.modules, env_obj, json_output=json_output)


def _module_update_execute(
    instance: Any,
    modules: list[str],
    env_obj: Any,
    *,
    json_output: bool,
) -> None:
    try:
        outcome = update_modules(instance, tuple(modules), env_id=str(env_obj.id))
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "module.update", str(e))
    if outcome.returncode != 0:
        fail(
            json_output,
            "module.update",
            f"shell exited {outcome.returncode}: {outcome.stderr.strip()}",
        )
    updated = outcome.payload.get("result", {}).get("updated", []) if outcome.payload else []
    if json_output:
        emit_json_envelope(
            ok=True, command="module.update", result={"updated": updated, "dry_run": False}
        )
    else:
        click.echo("Updated modules:")
        for m in updated:
            click.echo(f"  {m}")
    sys.exit(0)


@module_group.command("test")
@click.argument("modules", nargs=-1, required=True)
@click.option("--test-tags", "test_tags", required=True, help="Test tags.")
@click.option("--reload-tests", "reload_tests", is_flag=True, default=False)
@click.option("--allow-empty", "allow_empty", is_flag=True, default=False)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def module_test(
    ctx: click.Context,
    modules: tuple[str, ...],
    test_tags: str,
    reload_tests: bool,
    allow_empty: bool,
    json_output: bool,
) -> None:
    try:
        _client, env_obj, instance = cli_context.ready_instance(ctx)
        res, exit_code = run_module_tests(
            instance,
            tuple(modules),
            test_tags,
            reload_tests=reload_tests,
            allow_empty=allow_empty,
            env_id=str(env_obj.id),
            http_interface=env_obj.http_interface,
            http_port=env_obj.http_port,
        )
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "module.test", str(e))
    if json_output:
        emit_json_envelope(
            ok=True,
            command="module.test",
            result={
                "tests_count": res.tests_count,
                "tests_success": res.tests_success,
                "tests_errors": res.tests_errors,
                "tests_failed": res.tests_failed,
                "skipped": res.skipped,
                "had_failures": res.had_failures,
                "had_zero_tests": res.had_zero_tests,
                "allow_empty": allow_empty,
            },
        )
    else:
        click.echo(
            f"tests={res.tests_count} ok={res.tests_success} "
            f"failed={res.tests_failed} errors={res.tests_errors} skipped={res.skipped}"
        )
    sys.exit(exit_code)


@cli.group("translations")
def translations_group() -> None:
    pass


@translations_group.command("export")
@click.option("--module", "modules", multiple=True, required=True, help="Module name.")
@click.option("--language", "languages", multiple=True, required=True, help="Language code.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def translations_export(
    ctx: click.Context,
    modules: tuple[str, ...],
    languages: tuple[str, ...],
    json_output: bool,
) -> None:
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
        fail(json_output, "translations.export", str(e))
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
        )
    else:
        for r in results:
            click.echo(
                f"{r.module} {r.requested_lang} -> {r.actual_filename} "
                f"({r.bytes_written} bytes at {r.path})"
            )
    sys.exit(0)


@cli.group("deps")
def deps_group() -> None:
    pass


@deps_group.command("verify")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def deps_verify(ctx: click.Context, json_output: bool) -> None:
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
        fail(json_output, "deps.verify", str(e))
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
        )
    else:
        if result.pip_check_ok:
            click.echo("pip check: ok")
        else:
            click.echo("pip check: issues")
            for d in result.distributions:
                click.echo(f"  {d['detail']}")
        if result.missing_imports:
            click.echo("missing imports:")
            for m in result.missing_imports:
                click.echo(f"  {m['module']}: {m['import']}")
        else:
            click.echo("imports: ok")
    sys.exit(exit_code)


@cli.group("vscode")
def vscode_group() -> None:
    pass


@vscode_group.command("generate")
@click.option(
    "--write", "write_file", is_flag=True, default=False, help="Write .vscode/launch.json."
)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def vscode_generate(ctx: click.Context, write_file: bool, json_output: bool) -> None:
    try:
        client, env_obj, _instance = cli_context.ready_instance(ctx)
        profile = build_launch_profile(client, env_obj)
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "vscode.generate", str(e))
    if write_file:
        try:
            project_path = cli_context.resolve_project_path(ctx)
            content = launch_json(profile)
            written = write_launch_json(project_path, content)
        except Exception as e:
            fail(json_output, "vscode.generate", str(e))
        if json_output:
            emit_json_envelope(
                ok=True,
                command="vscode.generate",
                result={"profile": profile, "written": str(written), "dry_run": False},
            )
        else:
            click.echo(f"Wrote {written}")
        sys.exit(0)
        return
    if json_output:
        emit_json_envelope(
            ok=True, command="vscode.generate", result={"profile": profile, "dry_run": True}
        )
    else:
        click.echo(launch_json(profile), nl=False)
    sys.exit(0)


if __name__ == "__main__":
    cli()


# -- postgres cluster group -------------------------------------------------


@cli.group("postgres")
def postgres_group() -> None:
    """Project-level PostgreSQL cluster lifecycle (read-only / idempotent)."""


def _resolve_cluster(ctx: click.Context) -> PostgresCluster:
    project_path = cli_context.resolve_project_path(ctx)
    from odoo_instance_sdk.exceptions import OdooInstanceSdkError

    try:
        return PostgresCluster.from_project(project_path)
    except OdooInstanceSdkError as e:
        fail(False, "postgres", str(e))


def _cluster_state_to_exit(state: object) -> int:
    from odoo_instance_sdk.models import PostgresClusterState as _S

    if state == _S.HEALTHY:
        return 0
    return 1


@postgres_group.command("status")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def postgres_status(ctx: click.Context, json_output: bool) -> None:
    try:
        cluster = _resolve_cluster(ctx)
        state = cluster.status()
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "postgres.status", str(e))
    diag = dict(cluster.to_diagnostic_dict())
    diag["state"] = state.value
    if json_output:
        emit_json_envelope(
            ok=True,
            command="postgres.status",
            result=diag,
        )
    else:
        click.echo(
            f"mode={cluster.mode} owned={cluster.owned} state={state.value} endpoint={cluster.endpoint}"
        )
    sys.exit(_cluster_state_to_exit(state))


@postgres_group.command("up")
@click.option(
    "--wait-timeout",
    "wait_timeout",
    type=float,
    default=60.0,
    help="Seconds to wait for the cluster to become healthy.",
)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def postgres_up(ctx: click.Context, wait_timeout: float, json_output: bool) -> None:
    try:
        cluster = _resolve_cluster(ctx)
        cluster.ensure_running(timeout=wait_timeout)
        state = cluster.status()
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "postgres.up", str(e))
    diag = dict(cluster.to_diagnostic_dict())
    diag["state"] = state.value
    if json_output:
        emit_json_envelope(ok=True, command="postgres.up", result=diag)
    else:
        click.echo(f"postgres up: state={state.value} endpoint={cluster.endpoint}")
    sys.exit(0)


@postgres_group.command("stop")
@click.option(
    "--timeout",
    "timeout",
    type=float,
    default=30.0,
    help="Seconds to wait for graceful stop.",
)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def postgres_stop(ctx: click.Context, timeout: float, json_output: bool) -> None:
    try:
        cluster = _resolve_cluster(ctx)
        cluster.stop(timeout=timeout)
        state = cluster.status()
    except SystemExit:
        raise
    except Exception as e:
        fail(json_output, "postgres.stop", str(e))
    diag = dict(cluster.to_diagnostic_dict())
    diag["state"] = state.value
    if json_output:
        emit_json_envelope(ok=True, command="postgres.stop", result=diag)
    else:
        click.echo(f"postgres stop: state={state.value} endpoint={cluster.endpoint}")
    sys.exit(0)
