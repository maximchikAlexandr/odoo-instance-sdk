from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click

from odoo_instance_sdk.exceptions import VscodeImportError
from odoo_instance_sdk.internal.project_manifest import manifest_path, write_manifest
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.internal.vscode_import import import_vscode_launch
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient


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

    if option_state.odoo_bin is None:
        if no_input or json_output or dry_run:
            _fail(no_input, json_output, "Missing required option --odoo-bin")
            return
        option_state.odoo_bin = Path(click.prompt("Path to odoo-bin"))
        provenance["discovery"].append("odoo_bin")

    if not option_state.odoo_bin:
        _fail(no_input, json_output, "odoo_bin is required")
        return

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
    )

    existing = manifest_path(resolved_project)
    if existing.is_file() and _handle_existing_manifest(
        existing, resolved_project, config, no_input, json_output
    ):
        return

    if dry_run:
        if json_output:
            _emit_json(
                ok=True,
                command="init",
                data=_manifest_dict(config),
                provenance=provenance,
                dry_run=True,
            )
        else:
            click.echo("Dry run — no files written.")
            click.echo(config.to_manifest())
        return

    write_manifest(resolved_project, config)
    if json_output:
        _emit_json(
            ok=True,
            command="init",
            data=_manifest_dict(config),
            provenance=provenance,
            dry_run=False,
        )
    else:
        click.echo(f"Wrote {existing}")


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
        _fail(no_input, json_output, str(e))
        return None
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
        _fail(no_input, json_output, f"Existing manifest unreadable: {e}")
        return True
    if _manifest_dict(existing_cfg) == _manifest_dict(config):
        if json_output:
            _emit_json(
                ok=True, command="init", data=_manifest_dict(config), provenance={}, dry_run=True
            )
        else:
            click.echo("Manifest already up to date; no-op.")
        return True
    if no_input or json_output:
        _fail(
            no_input, json_output, "manifest exists and differs; remove it first or adjust options"
        )
        return True
    if not click.confirm("Manifest exists and differs; overwrite?", default=False):
        click.echo("Aborted.")
        return True
    return False


def _manifest_dict(config: ProjectConfig) -> dict[str, Any]:
    return {
        "odoo_bin": str(config.odoo_bin) if config.odoo_bin else None,
        "python": str(config.python) if config.python else None,
        "source_config": str(config.source_config) if config.source_config else None,
        "default_source_database": config.default_source_database,
        "preferred_http_port": config.preferred_http_port,
        "requirements": list(config.requirements),
        "default_run_args": list(config.default_run_args),
        "runtime_cwd": str(config.runtime_cwd) if config.runtime_cwd else None,
    }


def _sanitize_diagnostic(value: object) -> str:
    """Make every non-interactive diagnostic safe and bounded before emission."""
    return sanitize_last_error(str(value)) or "operation failed"


def _emit_json_envelope(
    *,
    ok: bool,
    command: str,
    result: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    dry_run: bool = False,
    error_code: str | None = None,
    error_message: object | None = None,
) -> None:
    envelope: dict[str, Any] = {
        "schema_version": 1,
        "ok": ok,
        "command": command,
        "context": context or {},
        "provenance": provenance or {},
        "dry_run": dry_run,
        "warnings": [],
    }
    if ok:
        safe_result = result or {}
        # `data` remains for the v1 consumers; `result` is the single stable
        # machine-output contract for new callers.
        envelope["result"] = safe_result
        envelope["data"] = safe_result
    else:
        envelope["error"] = {
            "code": error_code or command.replace(".", "_") + "_failed",
            "message": _sanitize_diagnostic(error_message),
        }
    click.echo(json.dumps(envelope, indent=2, default=str))


def _emit_json(
    *, ok: bool, command: str, data: dict[str, Any], provenance: dict[str, Any], dry_run: bool
) -> None:
    _emit_json_envelope(ok=ok, command=command, result=data, provenance=provenance, dry_run=dry_run)


def _fail(no_input: bool, json_output: bool, message: str) -> None:
    if json_output:
        _emit_json_envelope(
            ok=False,
            command="init",
            error_code="init_failed",
            error_message=message,
        )
    else:
        click.echo(_sanitize_diagnostic(message), err=True)
    sys.exit(1)


def _make_client() -> OdooClient:
    from odoo_instance_sdk import OdooClient, OdooClientConfig

    return OdooClient(config=OdooClientConfig(executable="odoo"))


def _resolve_project_path(ctx: click.Context) -> Path:
    from odoo_instance_sdk.internal.context import resolve_project

    raw = ctx.obj.get("project")
    project = resolve_project(Path(raw) if raw is not None else None)
    ctx.obj["project_source"] = "explicit" if raw is not None else "cwd_or_worktree"
    if isinstance(project, ProjectConfig):
        assert project.repository_root is not None
        return project.repository_root
    return Path(project)


@cli.group()
def env() -> None:
    pass


@env.command("checkout")
@click.argument("branch")
@click.option("--base", "base_ref", default=None, help="Base ref (default HEAD).")
@click.option(
    "--config", "config_path", type=click.Path(), default=None, help="Source odoo.conf path."
)
@click.option("--name", "name", default=None, help="Environment name.")
@click.option(
    "--db-mode",
    "db_mode",
    type=click.Choice(["shared", "copy"]),
    default="shared",
    help="Database mode.",
)
@click.option("--source-db", "source_database", default=None, help="Source database name.")
@click.option("--target-db", "target_database", default=None, help="Target database name.")
@click.option("--odoo-bin", "odoo_bin", type=click.Path(), default=None, help="Path to odoo-bin.")
@click.option("--python", "python", default=None, help="Python interpreter or uv selector.")
@click.option(
    "--create-venv", "create_venv", is_flag=True, default=False, help="Create owned venv."
)
@click.option("--http-port", "http_port", type=int, default=None, help="HTTP port.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def env_checkout(
    ctx: click.Context,
    branch: str,
    base_ref: str | None,
    config_path: str | None,
    name: str | None,
    db_mode: str,
    source_database: str | None,
    target_database: str | None,
    odoo_bin: str | None,
    python: str | None,
    create_venv: bool,
    http_port: int | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    from odoo_instance_sdk.resources.environment import (
        EnvironmentCheckoutOptions,
        EnvironmentDatabaseMode,
    )

    try:
        project_path = _resolve_project_path(ctx)
        client = _make_client()
        options = EnvironmentCheckoutOptions(
            base_ref=base_ref,
            name=name,
            config_path=Path(config_path) if config_path else None,
            db_mode=EnvironmentDatabaseMode(db_mode),
            source_database=source_database,
            target_database=target_database,
            odoo_bin=Path(odoo_bin) if odoo_bin else None,
            python=python,
            create_venv=create_venv,
            http_port=http_port,
        )
        result = (
            client.environments.plan_checkout(project_path, branch, options=options)
            if dry_run
            else client.environments.checkout(project_path, branch, options=options)
        )
    except Exception as e:
        _env_fail(json_output, "env.checkout", str(e))
        return
    if json_output:
        _env_json(
            "env.checkout",
            _checkout_plan_dict(result) if dry_run else _env_dict(result),
            dry_run=dry_run,
        )
    else:
        if dry_run:
            _print_plan("Checkout plan", _checkout_plan_dict(result))
        else:
            rendered = _env_dict(result)
            click.echo(
                f"Environment {rendered['name']} ({rendered['id']}) state={rendered['state']}"
            )
        if result.db_mode == EnvironmentDatabaseMode.SHARED:
            click.echo("Warning: code/process isolated, DB and filestore are NOT.")


@env.command("list")
@click.option("--all", "all_envs", is_flag=True, default=False, help="Include removed.")
@click.option(
    "--all-projects", "all_projects", is_flag=True, default=False, help="List all projects."
)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def env_list(ctx: click.Context, all_envs: bool, all_projects: bool, json_output: bool) -> None:
    client = _make_client()
    try:
        project = None if all_projects else _resolve_project_path(ctx)
        envs = client.environments.list(project=project, include_removed=all_envs)
    except Exception as e:
        _env_fail(json_output, "env.list", str(e))
        return
    if json_output:
        _env_json(
            "env.list",
            {"environments": [_reconcile_environment(e, client) for e in envs]},
            dry_run=False,
        )
    else:
        _print_env_table(envs, client)


@env.command("remove")
@click.argument("environment", required=False)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Skip confirmation.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def env_remove(
    ctx: click.Context, environment: str | None, dry_run: bool, yes: bool, json_output: bool
) -> None:
    client = _make_client()
    if environment is None:
        if ctx.obj.get("env") is not None:
            _usage_fail(
                json_output,
                "env.remove",
                "root --env is not accepted by env remove; pass ENVIRONMENT or cd into its worktree",
            )
        try:
            from odoo_instance_sdk.internal.context import resolve_environment

            env_obj = resolve_environment(client, None)
        except Exception as e:
            _env_fail(json_output, "env.remove", str(e))
            return
    else:
        try:
            _resolve_project_path(ctx)
            env_obj = client.environments.get(environment)
        except Exception as e:
            _env_fail(json_output, "env.remove", str(e))
            return
    if dry_run:
        if json_output:
            _env_json("env.remove", _remove_plan_dict(env_obj), dry_run=True)
        else:
            _print_plan("Remove plan", _remove_plan_dict(env_obj))
        return
    if not yes and not click.confirm(
        f"Remove environment {env_obj.name} ({env_obj.id})?", default=False
    ):
        click.echo("Aborted.")
        return
    try:
        client.environments.remove(env_obj)
        env_obj = client.environments.get(str(env_obj.id))
    except Exception as e:
        _env_fail(json_output, "env.remove", str(e))
        sys.exit(1)
    if json_output:
        _env_json("env.remove", _env_dict(env_obj), dry_run=False)
    else:
        click.echo(f"Removed environment {env_obj.name} ({env_obj.id})")


@env.command("sync")
@click.argument("environment", required=False)
@click.option("--upgrade", "upgrade", is_flag=True, default=False)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def env_sync(ctx: click.Context, environment: str | None, upgrade: bool, json_output: bool) -> None:
    client = _make_client()
    if environment is None:
        if ctx.obj.get("env") is not None:
            _usage_fail(
                json_output,
                "env.sync",
                "root --env is not accepted by env sync; pass ENVIRONMENT or cd into its worktree",
            )
        try:
            from odoo_instance_sdk.internal.context import resolve_environment

            environment = str(resolve_environment(client, None).id)
        except Exception as e:
            _env_fail(json_output, "env.sync", str(e))
            return
    try:
        _resolve_project_path(ctx)
        result = client.environments.sync_python(environment, upgrade=upgrade)
    except Exception as e:
        _env_fail(json_output, "env.sync", str(e))
        return
    if json_output:
        _env_json("env.sync", _env_dict(result), dry_run=False)
    else:
        click.echo(f"Synced environment {result.name} ({result.id}) state={result.state}")


def _env_dict(e: object) -> dict[str, Any]:
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment

    env = cast("DevelopmentEnvironment", e)
    return {
        "id": str(env.id),
        "name": env.name,
        "state": str(env.state),
        "branch": env.branch,
        "db_mode": str(env.db_mode),
        "http_port": env.http_port,
        "worktree_path": env.worktree_path,
    }


def _checkout_plan_dict(plan: object) -> dict[str, Any]:
    from odoo_instance_sdk.resources.environment import CheckoutPlan

    if not isinstance(plan, CheckoutPlan):
        raise TypeError("checkout dry-run must return CheckoutPlan")
    return {
        "id": str(plan.env_id),
        "name": plan.name,
        "state": str(plan.state),
        "branch": plan.branch,
        "db_mode": str(plan.db_mode),
        "http_port": plan.http_port,
        "worktree_path": str(plan.worktree),
        "generated_config_path": str(plan.generated_config),
        "dependency_lock_path": str(plan.dependency_lock),
        "python_mode": "create" if plan.python_owned else "reuse",
        "python_path": plan.python_path,
        "database": {
            "mode": str(plan.db_mode),
            "source": plan.source_database,
            "target": plan.target_database,
            "owned": plan.db_mode.value == "copy",
        },
        "ownership": {
            "worktree": True,
            "generated_config": True,
            "dependency_lock": True,
            "python_environment": plan.python_owned,
            "backup": False,
        },
        "commands": ["git worktree add", "generate odoo.conf", "uv pip compile"],
        "dependency_inputs": {
            "requirements": list(plan.dependency_inputs),
            "lock_path": str(plan.dependency_lock),
        },
        "helper_argv": {
            "git_worktree_add": list(plan.worktree_argv),
            "uv_compile": [
                "uv",
                "pip",
                "compile",
                *plan.dependency_inputs,
                "-o",
                str(plan.dependency_lock),
            ],
        },
    }


def _remove_plan_dict(e: object) -> dict[str, Any]:
    plan = _env_dict(e)
    plan["ownership"] = {"worktree": True, "generated_config": True, "backup": False}
    plan["commands"] = (
        ["drop target database", "delete owned backup", "git worktree remove"]
        if str(cast("Any", e).db_mode) == "copy"
        else ["git worktree remove"]
    )
    plan["ownership"]["backup"] = cast("Any", e).backup_id is not None
    return plan


def _print_plan(title: str, plan: dict[str, Any]) -> None:
    click.echo(title)
    for key, value in plan.items():
        click.echo(f"{key}: {json.dumps(value, default=str, sort_keys=True)}")


def _reconcile_environment(e: object, client: OdooClient) -> dict[str, Any]:
    env = cast("Any", e)
    worktree = Path(env.worktree_path)
    try:
        from odoo_instance_sdk.internal.git_worktree import worktree_list_porcelain

        registered = any(
            Path(entry.worktree).resolve() == worktree.resolve()
            for entry in worktree_list_porcelain(Path(env.repository_root))
        )
    except OSError:
        registered = False
    observed = "unknown"
    if env.state == "ready":
        observed = "port-free" if _check_port_free(env) else "port-occupied"
    python_path = Path(env.python_environment_path)
    python_exists = (
        (python_path / "bin" / "python").is_file()
        if env.python_environment_owned
        else python_path.is_file()
    )
    backup_exists: bool | None = None
    if env.backup_id is not None:
        backup_row = client.get_catalog().get_by_id(str(env.backup_id))
        backup_exists = (
            backup_row is not None
            and bool(backup_row["path"])
            and Path(str(backup_row["path"])).is_file()
        )
    lock_path = Path(env.dependency_lock_path)
    fingerprint = (
        hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.is_file() else None
    )
    root = Path(env.worktree_path).parent.resolve()
    python_contained = not env.python_environment_owned or python_path.resolve().is_relative_to(
        root
    )
    return {
        **_env_dict(env),
        "source_database": env.source_db_name,
        "target_database": env.target_db_name,
        "last_used": env.last_used_at,
        "observed": observed,
        "python_mode": "create" if env.python_environment_owned else "reuse",
        "reconciliation": {
            "worktree_exists": worktree.is_dir(),
            "worktree_registered": registered,
            "config_exists": Path(env.generated_config_path).is_file(),
            "python_exists": python_exists,
            "dependency_lock_exists": lock_path.is_file(),
            "dependency_fingerprint": fingerprint,
            "backup_exists": backup_exists,
            "python_contained": python_contained,
        },
    }


def _env_json(command: str, data: dict[str, Any], *, dry_run: bool) -> None:
    environment = data.get("id")
    _emit_json_envelope(
        ok=True,
        command=command,
        result=data,
        context={
            "environment_id": environment,
            "worktree_path": data.get("worktree_path"),
        },
        provenance={
            "project_source": "explicit_or_context",
            "environment_source": "selector_or_worktree" if environment is not None else None,
        },
        dry_run=dry_run,
    )


def _env_fail(json_output: bool, command: str, message: str) -> None:
    if json_output:
        _emit_json_envelope(
            ok=False,
            command=command,
            error_code=command.replace(".", "_") + "_failed",
            error_message=message,
        )
    else:
        click.echo(_sanitize_diagnostic(message), err=True)
    sys.exit(1)


def _usage_fail(json_output: bool, command: str, message: str) -> None:
    if json_output:
        _emit_json_envelope(
            ok=False, command=command, error_code="usage_error", error_message=message
        )
    else:
        click.echo(_sanitize_diagnostic(message), err=True)
    raise click.exceptions.Exit(2)


def _print_env_table(envs: list[Any], client: OdooClient) -> None:
    rows: list[str] = []
    for e in envs:
        data = _reconcile_environment(e, client)
        db = data["source_database"] if e.db_mode == "shared" else data["target_database"]
        reconciliation = data["reconciliation"]
        artifacts = (
            ",".join(
                name
                for name, value in (
                    ("worktree", reconciliation["worktree_exists"]),
                    ("registered", reconciliation["worktree_registered"]),
                    ("config", reconciliation["config_exists"]),
                    ("python", reconciliation["python_exists"]),
                    ("python-contained", reconciliation["python_contained"]),
                    ("lock", reconciliation["dependency_lock_exists"]),
                    ("backup", reconciliation["backup_exists"]),
                )
                if value is False
            )
            or "ok"
        )
        rows.append(
            f"{e.id}  {e.name}  {e.state}  {data['observed']}  {e.branch}  "
            f"{data['python_mode']}  {e.db_mode}  {db or ''}  {e.http_port}  "
            f"{artifacts}  {data['last_used'] or ''}  {e.worktree_path}"
        )
    click.echo(
        "ID  NAME  STATE  OBSERVED  BRANCH  PYTHON_MODE  DB_MODE  DATABASE  PORT  ARTIFACTS  LAST_USED  WORKTREE"
    )
    for r in rows:
        click.echo(r)


@cli.command()
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def doctor(ctx: click.Context, json_output: bool) -> None:
    from odoo_instance_sdk.internal.doctor import run_doctor

    project_path = _resolve_project_path(ctx)
    client = _make_client()
    try:
        report = run_doctor(client, project_path if project_path != Path.cwd() else None)
    except Exception as e:
        _emit_command_error(json_output, "doctor", str(e))
        sys.exit(1)
        return
    if json_output:
        _emit_json_envelope(
            ok=report.ok,
            command="doctor",
            context=report.context,
            result={
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status,
                        "detail": _sanitize_diagnostic(c.detail),
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
    from odoo_instance_sdk.internal.doctor import DoctorReport

    rep = cast("DoctorReport", report)
    current_env: str | None = None
    for c in rep.checks:
        if c.environment_id and c.environment_id != current_env:
            current_env = c.environment_id
            click.echo(f"\n[{current_env}] {c.environment_name or ''}")
        marker = {"ok": "OK", "warn": "WARN", "error": "ERROR", "info": "INFO"}.get(
            c.status, c.status
        )
        click.echo(f"  {marker:<5} {c.name}: {_sanitize_diagnostic(c.detail)}")


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        if not _check_port_free(env_obj):
            click.echo(
                f"port-conflict: {env_obj.http_interface}:{env_obj.http_port} is occupied "
                "(ownership unknown)",
                err=True,
            )
            sys.exit(1)
            return
        _record_use_event(client, env_obj)
        instance = client.instance.from_environment(env_obj)
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(False, "run", str(e))
        sys.exit(1)
        return
    try:
        exit_code = instance.run_foreground()
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        _emit_command_error(False, "run", str(e))
        exit_code = 1
    sys.exit(exit_code)


@cli.command()
@click.argument("odoo_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def shell(ctx: click.Context, odoo_args: tuple[str, ...]) -> None:
    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        instance = client.instance.from_environment(env_obj)
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(False, "shell", str(e))
        sys.exit(1)
        return
    try:
        exit_code = instance.shell(args=list(odoo_args))
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        _emit_command_error(False, "shell", str(e))
        exit_code = 1
    sys.exit(exit_code)


@cli.command("eval")
@click.argument("expression")
@click.option(
    "--commit", "commit", is_flag=True, default=False, help="Commit after eval (best-effort)."
)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def eval_cmd(ctx: click.Context, expression: str, commit: bool, json_output: bool) -> None:
    from odoo_instance_sdk.internal.automation import eval_expression

    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        instance = client.instance.from_environment(env_obj)
        outcome = eval_expression(instance, expression, commit=commit)
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(json_output, "eval", str(e))
        sys.exit(1)
        return
    if outcome.returncode != 0:
        _emit_command_error(
            json_output, "eval", f"shell exited {outcome.returncode}: {outcome.stderr.strip()}"
        )
        sys.exit(1)
        return
    result = outcome.payload.get("result") if outcome.payload else None
    if json_output:
        _emit_command_json("eval", {"result": result, "commit": commit})
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
    from odoo_instance_sdk.internal.automation import exec_script

    if script == "-":
        source = sys.stdin.read()
    else:
        p = Path(script)
        if not p.is_file():
            _emit_command_error(json_output, "exec", f"script not found: {script}")
            sys.exit(1)
            return
        try:
            source = p.read_text(encoding="utf-8")
        except OSError as e:
            _emit_command_error(json_output, "exec", f"cannot read script: {e}")
            sys.exit(1)
            return
    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        instance = client.instance.from_environment(env_obj)
        outcome = exec_script(instance, source, argv=tuple(script_args), commit=commit)
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(json_output, "exec", str(e))
        sys.exit(1)
        return
    if json_output:
        _emit_command_json(
            "exec",
            {
                "returncode": outcome.returncode,
                "stdout": outcome.stdout,
                "stderr": _sanitize_diagnostic(outcome.stderr) if outcome.stderr else "",
                "commit": commit,
            },
        )
    else:
        click.echo(outcome.stdout, nl=False)
        if outcome.stderr:
            click.echo(_sanitize_diagnostic(outcome.stderr), err=True, nl=False)
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
    from odoo_instance_sdk.internal.automation import list_modules

    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        instance = client.instance.from_environment(env_obj)
        records = list_modules(instance, names=tuple(modules), state=state)
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(json_output, "module.list", str(e))
        sys.exit(1)
        return
    if json_output:
        _emit_command_json("module.list", {"modules": [r.to_dict() for r in records]})
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
    from odoo_instance_sdk.internal.automation import plan_module_update

    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        instance = client.instance.from_environment(env_obj)
        plan = plan_module_update(instance, tuple(modules))
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(json_output, "module.update", str(e))
        sys.exit(1)
        return
    if plan.not_installed:
        _emit_command_error(
            json_output,
            "module.update",
            f"modules not installed: {', '.join(plan.not_installed)}",
        )
        sys.exit(1)
        return
    if dry_run:
        if json_output:
            _emit_command_json("module.update", {"modules": plan.modules, "dry_run": True})
        else:
            click.echo("Dry run — modules to update:")
            for m in plan.modules:
                click.echo(f"  {m}")
        sys.exit(0)
        return
    if not yes:
        _emit_command_error(json_output, "module.update", "module update requires --yes")
        sys.exit(1)
        return
    _module_update_execute(instance, plan.modules, env_obj, json_output=json_output)


def _module_update_execute(
    instance: Any,
    modules: list[str],
    env_obj: Any,
    *,
    json_output: bool,
) -> None:
    from odoo_instance_sdk.internal.automation import update_modules

    try:
        outcome = update_modules(instance, tuple(modules), env_id=str(env_obj.id))
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(json_output, "module.update", str(e))
        sys.exit(1)
        return
    if outcome.returncode != 0:
        _emit_command_error(
            json_output,
            "module.update",
            f"shell exited {outcome.returncode}: {outcome.stderr.strip()}",
        )
        sys.exit(1)
        return
    updated = outcome.payload.get("result", {}).get("updated", []) if outcome.payload else []
    if json_output:
        _emit_command_json("module.update", {"updated": updated, "dry_run": False})
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
    from odoo_instance_sdk.internal.automation import run_module_tests

    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        instance = client.instance.from_environment(env_obj)
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
        _emit_command_error(json_output, "module.test", str(e))
        sys.exit(1)
        return
    if json_output:
        _emit_command_json(
            "module.test",
            {
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
    from pathlib import Path as _Path

    from odoo_instance_sdk.internal.automation import export_translations

    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        instance = client.instance.from_environment(env_obj)
        results = export_translations(
            instance,
            tuple(modules),
            tuple(languages),
            worktree_root=_Path(env_obj.worktree_path),
        )
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(json_output, "translations.export", str(e))
        sys.exit(1)
        return
    if json_output:
        _emit_command_json(
            "translations.export",
            {
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
    from pathlib import Path as _Path

    from odoo_instance_sdk.internal.automation import verify_deps

    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        _verify_env_runtime(env_obj)
        recorded_python = _Path(env_obj.python_environment_path)
        if recorded_python.is_dir():
            recorded_python = recorded_python / "bin" / "python"
        result = verify_deps(
            recorded_python=recorded_python,
            worktree_root=_Path(env_obj.worktree_path),
        )
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(json_output, "deps.verify", str(e))
        sys.exit(1)
        return
    exit_code = 1 if result.missing_imports else 0
    if json_output:
        _emit_command_json(
            "deps.verify",
            {
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
    from odoo_instance_sdk.internal.vscode_generate import (
        build_launch_profile,
        launch_json,
        write_launch_json,
    )

    client = _make_client()
    try:
        env_obj = _resolve_ready_env(ctx, client)
        profile = build_launch_profile(client, env_obj)
    except SystemExit:
        raise
    except Exception as e:
        _emit_command_error(json_output, "vscode.generate", str(e))
        sys.exit(1)
        return
    if write_file:
        try:
            project_path = _resolve_project_path(ctx)
            content = launch_json(profile)
            written = write_launch_json(project_path, content)
        except Exception as e:
            _emit_command_error(json_output, "vscode.generate", str(e))
            sys.exit(1)
            return
        if json_output:
            _emit_command_json(
                "vscode.generate",
                {"profile": profile, "written": str(written), "dry_run": False},
            )
        else:
            click.echo(f"Wrote {written}")
        sys.exit(0)
        return
    if json_output:
        _emit_command_json("vscode.generate", {"profile": profile, "dry_run": True})
    else:
        click.echo(launch_json(profile), nl=False)
    sys.exit(0)


def _emit_command_json(command: str, data: dict[str, Any]) -> None:
    _emit_json_envelope(ok=True, command=command, result=data)


def _emit_command_error(json_output: bool, command: str, message: str) -> None:
    if json_output:
        _emit_json_envelope(
            ok=False,
            command=command,
            error_code=command.replace(".", "_") + "_failed",
            error_message=message,
        )
    else:
        click.echo(_sanitize_diagnostic(message), err=True)


def _resolve_ready_env(ctx: click.Context, client: OdooClient) -> Any:
    from odoo_instance_sdk.internal.context import resolve_environment
    from odoo_instance_sdk.resources.environment import EnvironmentState

    env_selector = ctx.obj.get("env")
    env_obj = resolve_environment(client, env_selector)
    if env_obj.state != EnvironmentState.READY:
        raise RuntimeError(
            f"Environment {env_obj.name} ({env_obj.id}) is not ready (state={env_obj.state})"
        )
    return env_obj


def _verify_env_runtime(env_obj: Any) -> None:
    from pathlib import Path

    worktree = Path(env_obj.worktree_path)
    if not worktree.is_dir():
        raise RuntimeError(f"worktree missing: {worktree}")
    config_path = Path(env_obj.generated_config_path)
    if not config_path.is_file():
        raise RuntimeError(f"generated config missing: {config_path}")
    py_path = Path(env_obj.python_environment_path)
    if env_obj.python_environment_owned:
        if not (py_path / "bin" / "python").exists():
            raise RuntimeError(f"recorded Python missing: {py_path / 'bin' / 'python'}")
    elif not py_path.exists():
        raise RuntimeError(f"recorded Python missing: {py_path}")


def _check_port_free(env_obj: Any) -> bool:
    from odoo_instance_sdk.internal.address import AddressState, probe_address

    return probe_address(env_obj.http_interface, env_obj.http_port) is AddressState.FREE


def _record_use_event(client: OdooClient, env_obj: Any) -> None:
    from datetime import UTC, datetime

    catalog = client.get_catalog()
    now = datetime.now(UTC).isoformat()
    catalog.update_environment(str(env_obj.id), {"last_used_at": now})
    catalog.add_environment_event(str(env_obj.id), "use", "succeeded")


if __name__ == "__main__":
    cli()
