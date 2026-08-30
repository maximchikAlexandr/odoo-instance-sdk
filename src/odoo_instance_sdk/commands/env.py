from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click
import msgspec
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from odoo_instance_sdk.commands.context import (
    CliContext,
    pass_cli_context,
    resolve_environment,
    resolve_project_path,
)
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    OutputMode,
    emit,
    emit_json_envelope,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    rich_print,
    run_or_preview,
    sanitize_diagnostic,
    sanitize_terminal_text,
    success_document,
)
from odoo_instance_sdk.exceptions import ProjectContextError
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.internal.git_worktree import (
    rev_parse_git_common_dir,
    rev_parse_toplevel,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import (
    ClusterMetrics,
    ClusterSnapshot,
    DevelopmentEnvironment,
    EnvironmentArtifacts,
    EnvironmentCheckoutPlan,
    EnvironmentCheckoutResult,
    EnvironmentDatabaseMode,
    EnvironmentSnapshot,
    EnvironmentState,
    GitActivity,
    GitActivityState,
    PidScope,
    PostgresClusterState,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

_ENV_LIST_COLUMNS = (
    "NAME",
    "BRANCH",
    "STATE",
    "RUNTIME",
    "OBSERVED",
    "ODOO_PID",
    "CPU",
    "RAM",
    "GIT_AHEAD",
    "GIT_DIFF",
    "SIZE",
    "DB_MODE",
    "DATABASE",
    "PORT",
    "ARTIFACTS",
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
@output_options
@pass_cli_context
def env_checkout(  # noqa: C901
    cli_ctx: CliContext,
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
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        from odoo_instance_sdk.execution import Command, ExecutionPlan
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            _checkout_public_plan,
        )

        project_path = resolve_project_path(cli_ctx)
        client = _client_class()(config=_client_config_class()(executable="odoo"))
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
        command = client.environments.checkout_command(project_path, branch, options=options)
        if isinstance(command, Command):
            plan = _checkout_public_plan(command)

            def checkout_rich(document: OutputDocument) -> str:
                payload = document.result
                if not isinstance(payload, dict):
                    return ""
                lines: list[str] = []
                environment = payload.get("environment")
                if isinstance(environment, dict):
                    lines.append(
                        f"Environment {environment.get('name')} "
                        f"({environment.get('id')}) state={environment.get('state')}"
                    )
                plan_data = payload.get("plan")
                if isinstance(plan_data, dict):
                    lines.extend(_plan_lines("Checkout plan", plan_data))
                return "\n".join(lines)

            _, captured = run_or_preview(
                lambda: command,
                command_name="env.checkout",
                mode=output_mode,
                dry_run=dry_run,
                result=lambda environment: model_to_dict(
                    EnvironmentCheckoutResult(
                        environment=cast("DevelopmentEnvironment", environment), plan=plan
                    )
                ),
                rich=checkout_rich,
            )
            if dry_run:
                return
            assert captured is not None
            result = EnvironmentCheckoutResult(
                environment=cast("DevelopmentEnvironment", captured), plan=plan
            )
        else:
            # Keep compatibility with lightweight third-party resource doubles
            # that predate the additive command sibling.
            result = (
                client.environments.plan_checkout(project_path, branch, options=options)
                if dry_run
                else client.environments.checkout_with_plan(project_path, branch, options=options)
            )
    except Exception as e:
        fail(output_mode, "env.checkout", e)
    data = model_to_dict(result)
    environment = result.environment if isinstance(result, EnvironmentCheckoutResult) else None
    checkout_db_mode = result.db_mode if isinstance(result, EnvironmentCheckoutPlan) else None

    def rich_projection(_document: OutputDocument) -> str:
        lines: list[str] = []
        if isinstance(result, (ExecutionPlan, EnvironmentCheckoutPlan)):
            lines.extend(_plan_lines("Checkout plan", data))
        else:
            assert isinstance(result, EnvironmentCheckoutResult)
            rendered = result.environment
            lines.append(f"Environment {rendered.name} ({rendered.id}) state={rendered.state}")
            lines.extend(_plan_lines("Checkout plan", model_to_dict(result.plan)))
        if checkout_db_mode == EnvironmentDatabaseMode.SHARED:
            lines.append("Warning: code/process isolated, DB and filestore are NOT.")
        return "\n".join(lines)

    emit(
        success_document(
            command="env.checkout",
            result=data,
            context=(
                {
                    "environment_id": str(environment.id),
                    "worktree_path": environment.worktree_path,
                }
                if environment is not None
                else {}
            ),
            provenance={
                "project_source": cli_ctx.project_source,
                "environment_source": "null",
            },
            dry_run=dry_run,
        ),
        output_mode,
        rich=rich_projection,
    )


@env_group.command("list")
@click.option("--all", "all_envs", is_flag=True, default=False, help="Include removed.")
@click.option(
    "--all-projects", "all_projects", is_flag=True, default=False, help="List all projects."
)
@click.option(
    "--watch", is_flag=True, default=False, help="Refresh the Rich inventory continuously."
)
@click.option(
    "--interval",
    type=float,
    default=2.0,
    show_default=True,
    help="Seconds between Rich inventory refreshes.",
)
@output_options
@pass_cli_context
def env_list(
    ctx: CliContext,
    all_envs: bool,
    all_projects: bool,
    watch: bool,
    interval: float,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    _validate_watch_options(output_mode, watch=watch, interval=interval)
    try:
        project_id = _resolve_monitor_project_id(ctx, all_projects)
        monitor = _monitor_class()()
    except Exception as e:
        fail(output_mode, "env.list", str(e))

    # Rich's ``--all`` view includes removed rows.  Machine output keeps the
    # established active-only ``--all`` contract until its format rollout
    # changes that behavior explicitly.
    include_removed = all_envs and not json_output
    if watch:
        try:
            _run_env_list_live(
                monitor,
                project_id=project_id,
                include_removed=include_removed,
                interval=interval,
            )
        except KeyboardInterrupt as exc:
            raise click.exceptions.Exit(130) from exc
        return
    try:
        snapshot = monitor.snapshot(project_id=project_id, include_removed=include_removed)
    except Exception as e:
        fail(output_mode, "env.list", str(e))

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
                "project_source": "null" if all_projects else ctx.project_source,
                "environment_source": "null",
            },
            mode=output_mode,
        )
        return

    # Human output: grouped by project, with cluster summary + environment rows.
    _print_env_list_human(snapshot)


def _validate_watch_options(output_mode: OutputMode, *, watch: bool, interval: float) -> None:
    """Reject live-mode combinations before resolving or collecting inventory."""
    if interval < 0.1:
        raise click.UsageError("--interval must be at least 0.1 seconds")
    if not watch:
        return
    if output_mode is not OutputMode.RICH:
        raise click.UsageError("--watch is only available with Rich output")
    if not Console().is_terminal:
        fail(OutputMode.RICH, "env.list", "--watch requires an interactive terminal")


def _run_env_list_live(
    monitor: EnvironmentMonitor,
    *,
    project_id: str | None,
    include_removed: bool,
    interval: float,
) -> None:
    """Run the foreground Rich refresh loop without creating background work."""
    last_renderable: Group | None = None
    with Live(None, transient=True) as live:
        while True:
            try:
                snapshot = monitor.snapshot(
                    project_id=project_id,
                    include_removed=include_removed,
                )
                last_renderable = _render_env_list_rich(snapshot)
                live.update(last_renderable, refresh=True)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if last_renderable is None:
                    fail(OutputMode.RICH, "env.list", str(exc))
                # Keep the last successful table in the live region and add a
                # bounded, sanitized retry diagnostic below it.
                live.update(
                    Group(
                        last_renderable,
                        Text(f"Retrying: {sanitize_diagnostic(exc)}", style="yellow"),
                    ),
                    refresh=True,
                )
            time.sleep(interval)


def _resolve_monitor_project_id(ctx: CliContext, all_projects: bool) -> str | None:
    if all_projects:
        return None
    # Outside a project, ``env list`` is the cross-project listing; this keeps
    # the command useful from a neutral working directory.
    try:
        project_path = resolve_project_path(ctx)
    except ProjectContextError:
        return None
    repo_root = rev_parse_toplevel(project_path)
    git_common = rev_parse_git_common_dir(repo_root)
    return f"project_{repo_key(repo_root, git_common)}"


def _print_env_list_human(snapshot: Snapshot) -> None:
    Console().print(_render_env_list_rich(snapshot))


def _render_env_list_rich(snapshot: Snapshot) -> Group:
    """Build the Rich inventory projection without collecting any data."""
    envs_by_project: dict[str, list[EnvironmentSnapshot]] = {}
    for env in snapshot.environments:
        envs_by_project.setdefault(env.project_id, []).append(env)

    sections: list[Text | Table] = []
    for project in sorted(snapshot.projects, key=lambda item: item.id):
        sections.append(Text(sanitize_terminal_text(f"Project {project.name}"), style="bold cyan"))
        cluster = project.cluster
        sections.append(
            Text(
                sanitize_terminal_text(
                    "  PostgreSQL  —" if cluster is None else _cluster_summary_line(cluster)
                ),
                style="dim",
            )
        )
        table = Table(show_header=True, box=None, pad_edge=False)
        for column in _ENV_LIST_COLUMNS:
            table.add_column(column, overflow="fold")
        project_envs = sorted(envs_by_project.get(project.id, ()), key=lambda item: item.id)
        for env in project_envs:
            table.add_row(*_rich_env_row(env))
        sections.append(table)
    return Group(*sections)


def _rich_env_row(env: EnvironmentSnapshot) -> tuple[Text, ...]:
    """Return all fifteen environment values with terminal-aware styles."""
    values = _env_row_values(env)
    state_style = {
        "ready": "green",
        "not_ready": "yellow",
        "stopped": "dim",
        "removed": "red",
    }.get(values[2])
    runtime_style = "green" if values[3] == "ready" else None
    observed_style = "green" if values[4] == "port-free" else "yellow"
    styled_values = (
        (values[0], None),
        (values[1], None),
        (values[2], state_style),
        (values[3], runtime_style),
        (values[4], observed_style if values[4] != "—" else "dim"),
        *[(value, None) for value in values[5:]],
    )
    return tuple(
        Text(sanitize_terminal_text(value), style=style or "") for value, style in styled_values
    )


def _env_row_values(env: EnvironmentSnapshot) -> tuple[str, ...]:
    if env.lifecycle_state is EnvironmentState.REMOVED:
        return _removed_env_row_values(env)
    return (
        env.name,
        env.branch,
        env.lifecycle_state.value,
        _runtime_str(env.runtime.state),
        _observed_str(env),
        _odoo_pid_str(env.runtime),
        f"{env.runtime.cpu_percent:.1f}%" if env.runtime.cpu_percent is not None else "—",
        _human_bytes(env.runtime.rss_bytes) if env.runtime.rss_bytes is not None else "—",
        _git_ahead_str(env.git),
        _git_diff_str(env.git),
        _size_str(env.storage),
        env.db_mode,
        env.database or "",
        _port_str(env),
        _artifacts_str(env.artifacts),
    )


def _removed_env_row_values(env: EnvironmentSnapshot) -> tuple[str, ...]:
    return (
        env.name,
        env.branch,
        "removed",
        "—",
        "—",
        "—",
        "—",
        "—",
        "—",
        "—",
        "—",
        env.db_mode,
        env.database or "",
        str(env.allocated_http_port) if env.allocated_http_port is not None else "—",
        _artifacts_str(env.artifacts),
    )


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


def _runtime_str(state: RuntimeState) -> str:
    return state.value


def _observed_str(env: EnvironmentSnapshot) -> str:
    if env.observed_port is None:
        return "—"
    return f"port-{env.observed_port.value}"


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


def _artifacts_str(artifacts: EnvironmentArtifacts) -> str:
    return (
        ",".join(
            name
            for name, value in (
                ("worktree", artifacts.worktree_exists),
                ("registered", artifacts.worktree_registered),
                ("config", artifacts.config_exists),
                ("python", artifacts.python_exists),
                ("python-contained", artifacts.python_contained),
                ("lock", artifacts.dependency_lock_exists),
                ("backup", artifacts.backup_exists),
            )
            if value is False
        )
        or "ok"
    )


def _client_class() -> Any:
    return getattr(sys.modules[__name__], "OdooClient")


def _client_config_class() -> Any:
    return getattr(sys.modules[__name__], "OdooClientConfig")


def _monitor_class() -> Any:
    return getattr(sys.modules[__name__], "EnvironmentMonitor")


def __getattr__(name: str) -> Any:
    """Resolve operation dependencies only when a command or test requests them."""
    if name == "OdooClient":
        from odoo_instance_sdk.client import OdooClient

        globals()[name] = OdooClient
        return OdooClient
    if name == "OdooClientConfig":
        from odoo_instance_sdk.config import OdooClientConfig

        globals()[name] = OdooClientConfig
        return OdooClientConfig
    if name == "EnvironmentCheckoutOptions":
        from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions

        globals()[name] = EnvironmentCheckoutOptions
        return EnvironmentCheckoutOptions
    if name == "EnvironmentMonitor":
        from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

        globals()[name] = EnvironmentMonitor
        return EnvironmentMonitor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _require_machine_confirmation(output_mode: OutputMode, yes: bool) -> None:
    if output_mode is OutputMode.RICH or yes:
        return
    emit_json_envelope(
        ok=False,
        command="env.remove",
        error_code="confirmation_required",
        error_message="env remove requires --yes in machine output mode",
        mode=output_mode,
    )
    raise click.exceptions.Exit(1)


@env_group.command("remove")
@click.argument("environment", required=False)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Skip confirmation.")
@output_options
@pass_cli_context
def env_remove(  # noqa: C901
    ctx: CliContext,
    environment: str | None,
    dry_run: bool,
    yes: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    client = _client_class()(config=_client_config_class()(executable="odoo"))
    if environment is None:
        if ctx.env is not None:
            fail(
                output_mode,
                "env.remove",
                "root --env is not accepted by env remove; pass ENVIRONMENT or cd into its worktree",
                usage=True,
            )
        try:
            env_obj = resolve_environment(client, None, cli_context=ctx)
        except Exception as e:
            fail(output_mode, "env.remove", str(e))
    else:
        try:
            resolve_project_path(ctx)
            env_obj = client.environments.get(environment)
            ctx.resolved_environment = env_obj
        except Exception as e:
            fail(output_mode, "env.remove", str(e))
    from odoo_instance_sdk.execution import Command

    try:
        candidate = client.environments.remove_command(env_obj)
    except AttributeError:
        candidate = None
    if isinstance(candidate, Command):

        def confirm_remove() -> None:
            _require_machine_confirmation(output_mode, yes)
            if not yes and not click.confirm(
                sanitize_terminal_text(f"Remove environment {env_obj.name} ({env_obj.id})?"),
                default=False,
            ):
                emit(
                    success_document(command="env.remove", result={"aborted": True}),
                    output_mode,
                    rich=lambda _document: "Aborted.",
                )
                raise click.exceptions.Exit(0)

        try:
            status, _removed = run_or_preview(
                lambda: candidate,
                command_name="env.remove",
                mode=output_mode,
                dry_run=dry_run,
                confirm=confirm_remove,
                result=lambda _value: _env_dict(client.environments.get(str(env_obj.id))),
                context={
                    "environment_id": str(env_obj.id),
                    "worktree_path": env_obj.worktree_path,
                },
                provenance={
                    "project_source": ctx.project_source,
                    "environment_source": "explicit" if environment else "cwd",
                },
                rich=lambda _document: f"Removed environment {env_obj.name} ({env_obj.id})",
            )
            if dry_run:
                return
        except Exception as e:
            fail(output_mode, "env.remove", e)
        sys.exit(status)
        return
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
                    "project_source": ctx.project_source,
                    "environment_source": "explicit" if environment else "cwd",
                },
                dry_run=True,
                mode=output_mode,
            )
        else:
            _print_plan("Remove plan", _remove_plan_dict(env_obj))
        return
    _require_machine_confirmation(output_mode, yes)
    if not yes and not click.confirm(
        sanitize_terminal_text(f"Remove environment {env_obj.name} ({env_obj.id})?"), default=False
    ):
        rich_print("Aborted.")
        return
    try:
        client.environments.remove(env_obj)
        env_obj = client.environments.get(str(env_obj.id))
    except Exception as e:
        fail(output_mode, "env.remove", str(e))
    if json_output:
        data = _env_dict(env_obj)
        emit_json_envelope(
            ok=True,
            command="env.remove",
            result=data,
            context={"environment_id": data.get("id"), "worktree_path": data.get("worktree_path")},
            provenance={
                "project_source": ctx.project_source,
                "environment_source": "explicit" if environment else "cwd",
            },
            mode=output_mode,
        )
    else:
        rich_print(f"Removed environment {env_obj.name} ({env_obj.id})")


@env_group.command("sync")
@click.argument("environment", required=False)
@click.option("--upgrade", "upgrade", is_flag=True, default=False)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@output_options
@pass_cli_context
def env_sync(
    ctx: CliContext,
    environment: str | None,
    upgrade: bool,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    json_output = output_mode is not OutputMode.RICH
    client = _client_class()(config=_client_config_class()(executable="odoo"))
    if environment is None:
        if ctx.env is not None:
            fail(
                output_mode,
                "env.sync",
                "root --env is not accepted by env sync; pass ENVIRONMENT or cd into its worktree",
                usage=True,
            )
        try:
            environment = str(resolve_environment(client, None, cli_context=ctx).id)
        except Exception as e:
            fail(output_mode, "env.sync", str(e))
    try:
        resolve_project_path(ctx)
        from odoo_instance_sdk.execution import Command

        candidate = client.environments.sync_python_command(environment, upgrade=upgrade)
        command = candidate if isinstance(candidate, Command) else None
    except Exception as e:
        fail(output_mode, "env.sync", str(e))
    if command is not None:
        status, result = run_or_preview(
            lambda: command,
            command_name="env.sync",
            mode=output_mode,
            dry_run=dry_run,
            result=_env_dict,
            rich=lambda document: (
                f"Synced environment {document.result.get('name')} "
                f"({document.result.get('id')}) state={document.result.get('state')}"
                if isinstance(document.result, dict)
                else ""
            ),
        )
        if dry_run:
            return
        assert result is not None
        sys.exit(status)
        return
    if dry_run:
        fail(output_mode, "env.sync", "environment does not provide an inspectable command")
    try:
        result = client.environments.sync_python(environment, upgrade=upgrade)
    except Exception as e:
        fail(output_mode, "env.sync", e)
    if json_output:
        data = _env_dict(result)
        emit_json_envelope(
            ok=True,
            command="env.sync",
            result=data,
            context={"environment_id": data.get("id"), "worktree_path": data.get("worktree_path")},
            provenance={},
            mode=output_mode,
        )
    else:
        rich_print(f"Synced environment {result.name} ({result.id}) state={result.state}")


def _env_dict(e: object) -> dict[str, Any]:
    env = cast("Any", e)
    return {
        "id": str(env.id),
        "name": env.name,
        "state": str(env.state),
        "branch": env.branch,
        "db_mode": str(env.db_mode),
        "http_port": env.http_port,
        "worktree_path": env.worktree_path,
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


def _plan_lines(title: str, plan: dict[str, Any]) -> list[str]:
    """Pure Rich projection for a structured domain/command plan."""
    return [
        title,
        *[
            f"{key}: {json.dumps(value, default=str, sort_keys=True)}"
            for key, value in plan.items()
        ],
    ]


def _print_plan(title: str, plan: dict[str, Any]) -> None:
    """Compatibility renderer for the legacy watch/list paths."""
    rich_print("\n".join(_plan_lines(title, plan)), preserve_newlines=True)
