from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, cast

import click

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.internal import context as cli_context
from odoo_instance_sdk.internal.cli_output import emit_json_envelope, fail
from odoo_instance_sdk.internal.git_worktree import worktree_list_porcelain
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    _CheckoutPlan,
)


@click.group()
def env_group() -> None:
    pass


@env_group.command("checkout")
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
    try:
        project_path = cli_context.resolve_project_path(ctx)
        client = OdooClient(config=OdooClientConfig(executable="odoo"))
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
            client.environments._plan_checkout(project_path, branch, options=options)
            if dry_run
            else client.environments.checkout(project_path, branch, options=options)
        )
    except Exception as e:
        fail(json_output, "env.checkout", str(e))
    if json_output:
        data = _checkout_plan_dict(result) if dry_run else _env_dict(result)
        emit_json_envelope(
            ok=True,
            command="env.checkout",
            result=data,
            context={"environment_id": data.get("id"), "worktree_path": data.get("worktree_path")},
            provenance={
                "project_source": ctx.obj.get("project_source", "null"),
                "environment_source": "null",
            },
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


@env_group.command("list")
@click.option("--all", "all_envs", is_flag=True, default=False, help="Include removed.")
@click.option(
    "--all-projects", "all_projects", is_flag=True, default=False, help="List all projects."
)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def env_list(ctx: click.Context, all_envs: bool, all_projects: bool, json_output: bool) -> None:
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    try:
        project = None if all_projects else cli_context.resolve_project_path(ctx)
        envs = client.environments.list(project=project, include_removed=all_envs)
    except Exception as e:
        fail(json_output, "env.list", str(e))
    if json_output:
        backup_ids = {backup.id for backup in client.backups.list()}
        data = {"environments": [_reconcile_environment(e, backup_ids=backup_ids) for e in envs]}
        emit_json_envelope(
            ok=True,
            command="env.list",
            result=data,
            provenance={
                "project_source": "null" if all_projects else ctx.obj.get("project_source", "null"),
                "environment_source": "null",
            },
        )
    else:
        backup_ids = {backup.id for backup in client.backups.list()}
        _print_env_table(envs, backup_ids=backup_ids)


@env_group.command("remove")
@click.argument("environment", required=False)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Skip confirmation.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def env_remove(
    ctx: click.Context, environment: str | None, dry_run: bool, yes: bool, json_output: bool
) -> None:
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    if environment is None:
        if ctx.obj.get("env") is not None:
            fail(
                json_output,
                "env.remove",
                "root --env is not accepted by env remove; pass ENVIRONMENT or cd into its worktree",
                usage=True,
            )
        try:
            env_obj = cli_context.resolve_environment(client, None)
        except Exception as e:
            fail(json_output, "env.remove", str(e))
    else:
        try:
            cli_context.resolve_project_path(ctx)
            env_obj = client.environments.get(environment)
        except Exception as e:
            fail(json_output, "env.remove", str(e))
    if dry_run:
        if json_output:
            data = _remove_plan_dict(env_obj)
            emit_json_envelope(
                ok=True,
                command="env.remove",
                result=data,
                context={
                    "environment_id": data.get("id"),
                    "worktree_path": data.get("worktree_path"),
                },
                provenance={
                    "project_source": ctx.obj.get("project_source", "null"),
                    "environment_source": "explicit" if environment else "worktree",
                },
                dry_run=True,
            )
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
        fail(json_output, "env.remove", str(e))
    if json_output:
        data = _env_dict(env_obj)
        emit_json_envelope(
            ok=True,
            command="env.remove",
            result=data,
            context={"environment_id": data.get("id"), "worktree_path": data.get("worktree_path")},
            provenance={
                "project_source": ctx.obj.get("project_source", "null"),
                "environment_source": "explicit" if environment else "worktree",
            },
        )
    else:
        click.echo(f"Removed environment {env_obj.name} ({env_obj.id})")


@env_group.command("sync")
@click.argument("environment", required=False)
@click.option("--upgrade", "upgrade", is_flag=True, default=False)
@click.option("--json", "json_output", is_flag=True, default=False, help="Emit JSON envelope.")
@click.pass_context
def env_sync(ctx: click.Context, environment: str | None, upgrade: bool, json_output: bool) -> None:
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    if environment is None:
        if ctx.obj.get("env") is not None:
            fail(
                json_output,
                "env.sync",
                "root --env is not accepted by env sync; pass ENVIRONMENT or cd into its worktree",
                usage=True,
            )
        try:
            environment = str(cli_context.resolve_environment(client, None).id)
        except Exception as e:
            fail(json_output, "env.sync", str(e))
    try:
        cli_context.resolve_project_path(ctx)
        result = client.environments.sync_python(environment, upgrade=upgrade)
    except Exception as e:
        fail(json_output, "env.sync", str(e))
    if json_output:
        data = _env_dict(result)
        emit_json_envelope(
            ok=True,
            command="env.sync",
            result=data,
            context={"environment_id": data.get("id"), "worktree_path": data.get("worktree_path")},
            provenance={},
        )
    else:
        click.echo(f"Synced environment {result.name} ({result.id}) state={result.state}")


def _env_dict(e: object) -> dict[str, Any]:
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
    if not isinstance(plan, _CheckoutPlan):
        raise TypeError("checkout dry-run must return an internal checkout plan")
    return {
        "id": str(plan.env_id),
        "name": plan.name,
        "state": "creating",
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


def _reconcile_environment(e: object, *, backup_ids: set[uuid.UUID]) -> dict[str, Any]:
    env = cast("Any", e)
    worktree = Path(env.worktree_path)
    try:
        registered = any(
            Path(entry.worktree).resolve() == worktree.resolve()
            for entry in worktree_list_porcelain(Path(env.repository_root))
        )
    except OSError:
        registered = False
    observed = "unknown"
    if env.state == "ready":
        observed = "port-free" if cli_context._check_port_free(env) else "port-occupied"
    python_path = Path(env.python_environment_path)
    python_exists = (
        (python_path / "bin" / "python").is_file()
        if env.python_environment_owned
        else python_path.is_file()
    )
    backup_exists_val = None if env.backup_id is None else env.backup_id in backup_ids
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
            "backup_exists": backup_exists_val,
            "python_contained": python_contained,
        },
    }


def _print_env_table(envs: list[Any], *, backup_ids: set[uuid.UUID]) -> None:
    rows: list[str] = []
    for e in envs:
        data = _reconcile_environment(e, backup_ids=backup_ids)
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
