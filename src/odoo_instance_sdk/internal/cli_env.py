from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, cast

import click
import msgspec

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.exceptions import ProjectContextError
from odoo_instance_sdk.internal import context as cli_context
from odoo_instance_sdk.internal.address import AddressState, probe_address
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.internal.cli_output import emit_json_envelope, fail
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_git_common_dir,
    rev_parse_toplevel,
    worktree_list_porcelain,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import (
    ClusterMetrics,
    ClusterSnapshot,
    EnvironmentSnapshot,
    GitActivity,
    GitActivityState,
    PidScope,
    PostgresClusterState,
    ProjectSummary,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)
from odoo_instance_sdk.resources.environment import (
    DevelopmentEnvironment,
    EnvironmentCheckoutOptions,
    EnvironmentDatabaseMode,
    EnvironmentState,
    _CheckoutPlan,
)
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor


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
        project_id = _resolve_monitor_project_id(ctx, all_projects)
        monitor = EnvironmentMonitor()
        snapshot = monitor.snapshot(project_id=project_id)
    except Exception as e:
        fail(json_output, "env.list", str(e))

    if json_output:
        # ponytail: --json always wraps the non-removed Snapshot only; --all does
        # NOT change the JSON payload. msgspec round-trips enums/datetimes to
        # plain JSON-safe builtins.
        result = msgspec.to_builtins(snapshot)
        emit_json_envelope(
            ok=True,
            command="env.list",
            result=result,
            provenance={
                "project_source": "null" if all_projects else ctx.obj.get("project_source", "null"),
                "environment_source": "null",
            },
        )
        return

    # Human output: grouped by project, with cluster summary + environment rows.
    backup_ids = {backup.id for backup in client.backups.list()}
    _print_env_list_human(
        client,
        snapshot,
        backup_ids=backup_ids,
        all_envs=all_envs,
        all_projects=all_projects,
        project_id=project_id,
    )


def _resolve_monitor_project_id(ctx: click.Context, all_projects: bool) -> str | None:
    if all_projects:
        return None
    # Outside a project, ``env list`` is the cross-project listing; this keeps
    # the command useful from a neutral working directory.
    try:
        project_path = cli_context.resolve_project_path(ctx)
    except ProjectContextError:
        return None
    repo_root = rev_parse_toplevel(project_path)
    git_common = rev_parse_git_common_dir(repo_root)
    return f"project_{repo_key(repo_root, git_common)}"


def _print_env_list_human(
    client: OdooClient,
    snapshot: Snapshot,
    *,
    backup_ids: set[uuid.UUID],
    all_envs: bool,
    all_projects: bool,
    project_id: str | None,
) -> None:
    envs_by_project: dict[str, list[EnvironmentSnapshot]] = {}
    for env in snapshot.environments:
        envs_by_project.setdefault(env.project_id, []).append(env)

    # A context-resolved listing is a project view, including its removed rows;
    # only --all-projects may surface removed rows from other projects.
    visible_project_ids = None if all_projects or project_id is None else {project_id}
    removed_by_project = (
        _removed_environments_by_project(client, visible_project_ids) if all_envs else {}
    )

    printed_ids: set[str] = set()
    for project in snapshot.projects:
        printed_ids.add(project.id)
        _print_project_header(project)
        project_envs = envs_by_project.get(project.id, [])
        for env in project_envs:
            catalog_env = _lookup_catalog_env(client, env.id)
            click.echo(_format_env_row(env, catalog_env, backup_ids=backup_ids))
        for env_obj in sorted(removed_by_project.get(project.id, []), key=lambda item: item.name):
            click.echo(_format_removed_row(env_obj, backup_ids=backup_ids))

    _print_removed_only_projects(removed_by_project, printed_ids, backup_ids)


def _removed_environments_by_project(
    client: OdooClient,
    visible_project_ids: set[str] | None = None,
) -> dict[str, list[DevelopmentEnvironment]]:
    grouped: dict[str, list[DevelopmentEnvironment]] = {}
    try:
        environments = client.environments.list(include_removed=True)
    except Exception:
        return grouped
    for env_obj in environments:
        if env_obj.state != EnvironmentState.REMOVED:
            continue
        project_id = (
            f"project_{repo_key(Path(env_obj.repository_root), Path(env_obj.git_common_dir))}"
        )
        if visible_project_ids is not None and project_id not in visible_project_ids:
            continue
        grouped.setdefault(project_id, []).append(env_obj)
    return grouped


def _print_removed_only_projects(
    removed_by_project: dict[str, list[DevelopmentEnvironment]],
    printed_ids: set[str],
    backup_ids: set[uuid.UUID],
) -> None:
    """Render removed-only projects which are absent from the live snapshot."""
    for project_id in sorted(set(removed_by_project) - printed_ids):
        env_objs = removed_by_project[project_id]
        click.echo(f"Project {Path(env_objs[0].repository_root).name}")
        click.echo("  PostgreSQL  —")
        for env_obj in sorted(env_objs, key=lambda item: item.name):
            click.echo(_format_removed_row(env_obj, backup_ids=backup_ids))


def _print_project_header(project: ProjectSummary) -> None:
    click.echo(f"Project {project.name}")
    cluster = project.cluster
    if cluster is None:
        click.echo("  PostgreSQL  —")
        return
    click.echo(_cluster_summary_line(cluster))


def _cluster_summary_line(cluster: ClusterSnapshot) -> str:
    parts = ["  PostgreSQL", cluster.state.value]
    if cluster.unavailability_reason and cluster.unavailability_reason not in {
        "external_not_owned"
    }:
        parts.append(cluster.unavailability_reason)
        return "  ".join(parts)
    parts.extend(_cluster_identity_parts(cluster))
    parts.extend(_cluster_metrics_parts(cluster.metrics))
    return "  ".join(parts)


def _cluster_identity_parts(cluster: ClusterSnapshot) -> list[str]:
    out: list[str] = []
    container = cluster.container
    if container is not None and container.id is not None:
        out.append(f"container={container.id[:12]}")
        if container.pid is not None:
            scope_prefix = "vm" if container.pid_scope is PidScope.DOCKER_VM else "host"
            out.append(f"pid={scope_prefix}:{container.pid}")
    elif cluster.mode == "external":
        out.append("external")
    elif cluster.state is PostgresClusterState.STOPPED:
        out.append("stopped")
    elif cluster.container is None:
        out.append("missing")
    return out


def _cluster_metrics_parts(metrics: ClusterMetrics | None) -> list[str]:
    if metrics is None:
        return []
    out: list[str] = []
    if metrics.cpu_percent is not None:
        out.append(f"cpu={metrics.cpu_percent:.1f}%")
    if metrics.memory_usage_bytes is not None:
        out.append(f"ram={_human_bytes(metrics.memory_usage_bytes)}")
    if metrics.volume_usage_bytes is not None:
        out.append(f"disk={_human_bytes(metrics.volume_usage_bytes)}")
    return out


def _lookup_catalog_env(client: OdooClient, env_id: str) -> DevelopmentEnvironment | None:
    try:
        return client.environments.get(env_id)
    except Exception:
        return None


def _format_env_row(
    env: EnvironmentSnapshot,
    catalog_env: DevelopmentEnvironment | None,
    *,
    backup_ids: set[uuid.UUID],
) -> str:
    name = env.name
    branch = env.branch
    state = env.lifecycle_state.value
    runtime = _runtime_str(env.runtime.state)
    observed = _observed_str(env, catalog_env)
    odoo_pid = _odoo_pid_str(env.runtime)
    cpu = f"{env.runtime.cpu_percent:.1f}%" if env.runtime.cpu_percent is not None else "—"
    ram = _human_bytes(env.runtime.rss_bytes) if env.runtime.rss_bytes is not None else "—"
    git_ahead = _git_ahead_str(env.git)
    git_diff = _git_diff_str(env.git)
    size = _size_str(env.storage)
    port = _port_str(env)
    database = env.database or ""
    artifacts = _artifacts_str(catalog_env, backup_ids=backup_ids)
    return (
        f"{name}  {branch}  {state}  {runtime}  {observed}  {odoo_pid}  {cpu}  {ram}  "
        f"{git_ahead}  {git_diff}  {size}  {env.db_mode}  {database}  {port}  {artifacts}"
    )


def _format_removed_row(env_obj: DevelopmentEnvironment, *, backup_ids: set[uuid.UUID]) -> str:
    artifacts = _artifacts_str(env_obj, backup_ids=backup_ids)
    db = (
        env_obj.source_db_name
        if env_obj.db_mode == EnvironmentDatabaseMode.SHARED
        else env_obj.target_db_name
    )
    port = "—"
    try:
        from odoo_instance_sdk.models import StartConfig

        parsed = StartConfig.from_odoo_config(str(env_obj.generated_config_path))
        if parsed.http_port is not None:
            port = str(parsed.http_port)
    except Exception:
        pass
    # Keep the canonical 15-column shape even though removed rows have no
    # monitor metrics.  This is deliberately positional for shell users.
    return (
        f"{env_obj.name}  {env_obj.branch}  removed  —  —  —  —  —  —  —  —  "
        f"{env_obj.db_mode}  {db or ''}  {port}  {artifacts}"
    )


def _runtime_str(state: RuntimeState) -> str:
    return state.value


def _observed_str(env: EnvironmentSnapshot, catalog_env: DevelopmentEnvironment | None) -> str:
    if env.lifecycle_state != EnvironmentState.READY:
        return "—"
    if env.runtime.state != RuntimeState.READY:
        return "—"
    if env.allocated_http_port is None or catalog_env is None:
        return "—"
    interface = catalog_env.http_interface
    port = env.allocated_http_port
    return "port-free" if _probe_port_free(interface, port) else "port-occupied"


def _probe_port_free(interface: str, port: int) -> bool:
    # ponytail: thin shim over probe_address; reused from cli_context to avoid
    # building a fake env object just to call _check_port_free.
    return probe_address(interface, port) is AddressState.FREE


def _odoo_pid_str(runtime: RuntimeMetrics) -> str:
    if runtime.state == RuntimeState.STOPPED or runtime.root_pid is None:
        return "—"
    child = len(runtime.child_pids)
    return f"{runtime.root_pid} (+{child})" if child else str(runtime.root_pid)


def _git_ahead_str(git: GitActivity) -> str:
    if git.state == GitActivityState.ORPHAN or git.ahead is None or git.behind is None:
        return "—"
    return f"↑{git.ahead} ↓{git.behind}"


def _git_diff_str(git: GitActivity) -> str:
    if git.diff is None:
        return "—"
    return f"+{git.diff.added} -{git.diff.deleted}"


def _size_str(storage: StorageFootprint) -> str:
    prefix = ">=" if not storage.complete else ""
    return f"{prefix}{_human_bytes(storage.total_bytes)}"


def _port_str(env: EnvironmentSnapshot) -> str:
    if env.runtime.state in (RuntimeState.READY, RuntimeState.NOT_READY):
        return str(env.runtime.http_port) if env.runtime.http_port is not None else "—"
    return str(env.allocated_http_port) if env.allocated_http_port is not None else "—"


def _artifacts_str(
    catalog_env: DevelopmentEnvironment | None, *, backup_ids: set[uuid.UUID]
) -> str:
    if catalog_env is None:
        return "—"
    data = _reconcile_environment(catalog_env, backup_ids=backup_ids)
    reconciliation = data["reconciliation"]
    return (
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
