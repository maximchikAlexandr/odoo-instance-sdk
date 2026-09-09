from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
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
    JsonObject,
    OutputDocument,
    OutputMode,
    emit,
    emit_json_envelope,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    sanitize_diagnostic,
    sanitize_terminal_text,
    success_document,
)
from odoo_instance_sdk.exceptions import BackupCatalogError, ProjectContextError, StalePlanError
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.internal.git_worktree import (
    local_branch_names,
    remote_branch_names,
    rev_parse_git_common_dir,
    rev_parse_toplevel,
)
from odoo_instance_sdk.internal.locks import exclusive_lock, provisioning_lock_path
from odoo_instance_sdk.internal.paths import get_catalog_path
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
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions
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
    "WORKTREE",
)

_JIRA_TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-[1-9][0-9]*\Z")
_JIRA_EVIDENCE_LIMIT = 32


class _JiraTicketType(click.ParamType[str]):
    name = "JIRA_TICKET"

    def convert(
        self, value: object, param: click.Parameter | None, ctx: click.Context | None
    ) -> str:
        ticket = str(value)
        if _JIRA_TICKET_RE.fullmatch(ticket) is None:
            self.fail("expected Jira ticket like PROJ-123", param, ctx)
        return ticket


_JIRA_TICKET = _JiraTicketType()


@dataclass(frozen=True, slots=True)
class _JiraAllocation:
    ticket: str
    branch: str
    repo_root: Path
    git_common_dir: Path
    base_ref: str
    local_heads: tuple[str, ...]
    catalogue_heads: tuple[str, ...]
    remote_heads: tuple[str, ...]


def _jira_iteration(ticket: str, branch: str) -> int | None:
    if branch == ticket:
        return 0
    match = re.fullmatch(rf"{re.escape(ticket)}_([1-9][0-9]*)", branch)
    return int(match.group(1)) if match is not None else None


def _catalogue_branch_names(
    client: object, repo_root: Path, git_common_dir: Path
) -> tuple[str, ...]:
    catalog = cast("OdooClient", client).get_catalog()
    names: set[str] = set()
    for row in catalog.list_environments(git_common_dir=str(git_common_dir), include_removed=True):
        if (
            Path(str(row["repository_root"])).resolve() == repo_root
            and Path(str(row["git_common_dir"])).resolve() == git_common_dir
        ):
            branch = row["branch"]
            if isinstance(branch, str):
                names.add(branch)
    return tuple(sorted(names))


def _resolve_jira_allocation(
    client: object,
    project_path: Path,
    ticket: str,
    base_ref_override: str | None,
) -> _JiraAllocation:
    repo_root = rev_parse_toplevel(project_path)
    git_common_dir = rev_parse_git_common_dir(repo_root)
    project = ProjectConfig.load(repo_root)
    base_ref = base_ref_override or project.default_base_ref or "HEAD"
    local_heads = tuple(sorted(set(local_branch_names(repo_root))))
    catalogue_heads = tuple(sorted(set(_catalogue_branch_names(client, repo_root, git_common_dir))))
    remote_heads = tuple(sorted(set(remote_branch_names(repo_root, ticket))))
    all_heads = (*local_heads, *catalogue_heads, *remote_heads)
    iterations = [
        iteration
        for branch in all_heads
        if (iteration := _jira_iteration(ticket, branch)) is not None
    ]
    next_iteration = max(iterations, default=-1) + 1
    branch = ticket if next_iteration == 0 else f"{ticket}_{next_iteration}"
    return _JiraAllocation(
        ticket=ticket,
        branch=branch,
        repo_root=repo_root,
        git_common_dir=git_common_dir,
        base_ref=base_ref,
        local_heads=local_heads,
        catalogue_heads=catalogue_heads,
        remote_heads=remote_heads,
    )


def _revalidate_jira_absence(client: object, allocation: _JiraAllocation) -> None:
    sources = (
        ("local", local_branch_names(allocation.repo_root)),
        (
            "catalogue",
            _catalogue_branch_names(client, allocation.repo_root, allocation.git_common_dir),
        ),
        ("origin", remote_branch_names(allocation.repo_root, allocation.ticket)),
    )
    for source, branches in sources:
        if allocation.branch in branches:
            raise StalePlanError(
                "Jira branch allocation became stale",
                expected={"branch": allocation.branch, "absent": True},
                actual={"source": source, "branch": allocation.branch},
            )


def _bounded_jira_heads(heads: tuple[str, ...]) -> JsonObject:
    return {
        "heads": list(heads[:_JIRA_EVIDENCE_LIMIT]),
        "total": len(heads),
        "truncated": len(heads) > _JIRA_EVIDENCE_LIMIT,
    }


def _jira_provenance(allocation: _JiraAllocation) -> JsonObject:
    return {
        "jira": {
            "ticket": allocation.ticket,
            "resolved_branch": allocation.branch,
            "base_ref": allocation.base_ref,
            "evidence": {
                "local": _bounded_jira_heads(allocation.local_heads),
                "catalogue": _bounded_jira_heads(allocation.catalogue_heads),
                "origin": _bounded_jira_heads(allocation.remote_heads),
            },
        }
    }


def _jira_rich_lines(document: OutputDocument) -> list[str]:
    if not isinstance(document.provenance, dict):
        return []
    jira = document.provenance.get("jira")
    if not isinstance(jira, dict):
        return []
    lines = [
        f"Jira {jira.get('ticket')} -> {jira.get('resolved_branch')} (base {jira.get('base_ref')})"
    ]
    evidence = jira.get("evidence")
    if isinstance(evidence, dict):
        for source in ("local", "catalogue", "origin"):
            source_data = evidence.get(source)
            if isinstance(source_data, dict):
                heads = source_data.get("heads")
                rendered_heads = (
                    ", ".join(str(head) for head in heads) if isinstance(heads, list) else ""
                )
                suffix = ", truncated" if source_data.get("truncated") else ""
                lines.append(
                    f"Jira {source}: [{rendered_heads}] "
                    f"({source_data.get('total')} captured head(s){suffix})"
                )
    return lines


def _jira_checkout_command(
    client: object,
    project_path: Path,
    options: EnvironmentCheckoutOptions,
    allocation: _JiraAllocation,
) -> Command[DevelopmentEnvironment]:
    selected_options = msgspec.structs.replace(options, base_ref=allocation.base_ref)
    from odoo_instance_sdk.resources.environment import EnvironmentResource

    environments = cast("OdooClient", client).environments
    if isinstance(environments, EnvironmentResource):
        return environments._checkout_command_with_branch_revalidation(
            project_path,
            allocation.branch,
            options=selected_options,
            branch_revalidator=lambda: _revalidate_jira_absence(client, allocation),
        )
    return environments.checkout_command(project_path, allocation.branch, options=selected_options)


def _build_jira_checkout_command(
    client: object,
    project_path: Path,
    jira_ticket: str,
    base_ref: str | None,
    options: EnvironmentCheckoutOptions,
) -> tuple[Command[DevelopmentEnvironment], _JiraAllocation | None]:
    from odoo_instance_sdk.resources.environment import EnvironmentResource

    if not isinstance(cast("OdooClient", client).environments, EnvironmentResource):
        return (
            cast("OdooClient", client).environments.checkout_command(
                project_path, jira_ticket, options=options
            ),
            None,
        )
    with exclusive_lock(provisioning_lock_path()):
        allocation = _resolve_jira_allocation(
            client, project_path, jira_ticket, base_ref_override=base_ref
        )
        return _jira_checkout_command(client, project_path, options, allocation), allocation


@click.group(help="Manage isolated development environments.")
def env_group() -> None:
    pass


@env_group.command(
    "checkout",
    aliases=["create"],
    help="Create an isolated environment from a Jira ticket branch.",
)
@click.argument("jira_ticket", type=_JIRA_TICKET)
@click.option("--base", "base_ref", default=None, help="Base ref (default HEAD).")
@click.option(
    "--config", "config_path", type=click.Path(), default=None, help="Source odoo.conf path."
)
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
def env_checkout(
    cli_ctx: CliContext,
    jira_ticket: str,
    base_ref: str | None,
    config_path: str | None,
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
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            _checkout_public_plan,
        )

        project_path = resolve_project_path(cli_ctx)
        client = _client_class()(config=_client_config_class()(executable="odoo"))
        options = EnvironmentCheckoutOptions(
            base_ref=base_ref,
            config_path=Path(config_path) if config_path else None,
            db_mode=EnvironmentDatabaseMode(db_mode),
            source_database=source_database,
            target_database=target_database,
            odoo_bin=Path(odoo_bin) if odoo_bin else None,
            python=python,
            create_venv=create_venv,
            http_port=http_port,
        )
        command, allocation = _build_jira_checkout_command(
            client, project_path, jira_ticket, base_ref, options
        )
        plan = _checkout_public_plan(command)
        jira_provenance = _jira_provenance(allocation) if allocation is not None else None

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
            lines.extend(_jira_rich_lines(document))
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
            emit_normal=False,
            provenance=jira_provenance,
            rich=checkout_rich,
            progress=True,
        )
        if dry_run:
            return
        assert captured is not None
        result = EnvironmentCheckoutResult(environment=captured, plan=plan)
    except Exception as e:
        fail(output_mode, "env.checkout", e, dry_run=dry_run)
    data = model_to_dict(result)
    environment = result.environment if isinstance(result, EnvironmentCheckoutResult) else None
    checkout_db_mode = result.db_mode if isinstance(result, EnvironmentCheckoutPlan) else None

    def rich_projection(document: OutputDocument) -> str:
        lines: list[str] = []
        if isinstance(result, (ExecutionPlan, EnvironmentCheckoutPlan)):
            lines.extend(_plan_lines("Checkout plan", data))
        else:
            assert isinstance(result, EnvironmentCheckoutResult)
            rendered = result.environment
            lines.append(f"Environment {rendered.name} ({rendered.id}) state={rendered.state}")
            lines.extend(_plan_lines("Checkout plan", model_to_dict(result.plan)))
        lines.extend(_jira_rich_lines(document))
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
                "project_source": _project_provenance(cli_ctx),
                "environment_source": "null",
                **(jira_provenance or {}),
            },
            dry_run=dry_run,
        ),
        output_mode,
        rich=rich_projection,
    )


@env_group.command(
    "list", aliases=["ls"], help="List initialized environments and their runtime state."
)
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
    _validate_watch_options(output_mode, watch=watch, interval=interval)
    try:
        project_id = _resolve_monitor_project_id(ctx, all_projects)
        monitor = _monitor_class()()
    except Exception as e:
        fail(output_mode, "env.list", str(e), dry_run=False)

    # Rich's ``--all`` view includes removed rows.  Machine output keeps the
    # established active-only ``--all`` contract until its format rollout
    # changes that behavior explicitly.
    machine_output = output_mode is not OutputMode.RICH
    include_removed = all_envs and not machine_output
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
        fail(output_mode, "env.list", str(e), dry_run=False)
    try:
        worktree_paths = (
            _catalog_worktree_paths(monitor, include_removed=include_removed)
            if snapshot.environments
            else {}
        )
    except Exception as e:
        fail(output_mode, "env.list", e, dry_run=False)

    if machine_output:
        # ponytail: --json always wraps the non-removed Snapshot only; --all does
        # NOT change the JSON payload. msgspec round-trips enums/datetimes to
        # plain JSON-safe builtins.
        result = msgspec.to_builtins(snapshot)
        try:
            _add_cli_worktree_paths(result, worktree_paths)
        except Exception as e:
            fail(output_mode, "env.list", e, dry_run=False)
        emit_json_envelope(
            ok=True,
            command="env.list",
            result=result,
            provenance={
                "project_source": "null" if all_projects else _project_provenance(ctx),
                "environment_source": "null",
            },
            mode=output_mode,
        )
        return

    # Human output: grouped by project, with cluster summary + environment rows.
    _print_env_list_human(snapshot, worktree_paths)


def _validate_watch_options(output_mode: OutputMode, *, watch: bool, interval: float) -> None:
    """Reject live-mode combinations before resolving or collecting inventory."""
    if interval < 0.1:
        raise click.UsageError("--interval must be at least 0.1 seconds")
    if not watch:
        return
    if output_mode is not OutputMode.RICH:
        raise click.UsageError("--watch is only available with Rich output")
    if not Console().is_terminal:
        fail(
            OutputMode.RICH,
            "env.list",
            "--watch requires an interactive terminal",
            dry_run=False,
        )


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
                worktree_paths = (
                    _catalog_worktree_paths(monitor, include_removed=include_removed)
                    if snapshot.environments
                    else {}
                )
                last_renderable = _render_env_list_rich(snapshot, worktree_paths)
                live.update(last_renderable, refresh=True)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if last_renderable is None:
                    fail(OutputMode.RICH, "env.list", str(exc), dry_run=False)
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


def _catalog_worktree_paths(
    monitor: EnvironmentMonitor, *, include_removed: bool
) -> dict[str, str]:
    """Read stored CLI-only paths after the monitor's single snapshot pass."""
    catalog_path = getattr(monitor, "catalog_path", None)
    if catalog_path is None:
        catalog_path = get_catalog_path(ensure_exists=False)
    catalog_path = Path(catalog_path)
    try:
        if not catalog_path.is_file():
            raise RuntimeError(
                "environment catalogue unavailable; cannot resolve worktree paths for env list"
            )
    except OSError as exc:
        raise RuntimeError(
            "environment catalogue unavailable; cannot resolve worktree paths for env list"
        ) from exc
    try:
        catalog = BackupCatalog(db_path=catalog_path)
        try:
            rows = catalog.list_environments(include_removed=include_removed)
        finally:
            catalog.close()
    except (BackupCatalogError, OSError) as exc:
        raise RuntimeError(
            "environment catalogue read failed; cannot resolve worktree paths for env list"
        ) from exc
    return {
        str(row["id"]): worktree_path
        for row in rows
        if isinstance(row["worktree_path"], str)
        and (worktree_path := row["worktree_path"].strip())
        and Path(worktree_path).is_absolute()
    }


def _add_cli_worktree_paths(result: JsonObject, worktree_paths: dict[str, str]) -> None:
    """Add only the approved CLI projection field to machine environment rows."""
    if not isinstance(result, dict):
        raise TypeError("env list projection is not an environment result object")
    environments = result.get("environments")
    if not isinstance(environments, (list, tuple)):
        raise TypeError("env list projection has no environment rows")
    for environment in environments:
        if not isinstance(environment, dict):
            raise TypeError("environment catalogue join returned an invalid environment row")
        environment_id = environment.get("id")
        worktree_path = worktree_paths.get(str(environment_id))
        if not isinstance(environment_id, str) or worktree_path is None:
            raise RuntimeError(
                "environment catalogue is missing a worktree path for an environment result"
            )
        environment["worktree_path"] = worktree_path


def _print_env_list_human(snapshot: Snapshot, worktree_paths: dict[str, str] | None = None) -> None:
    Console().print(_render_env_list_rich(snapshot, worktree_paths))


def _project_provenance(cli_context: CliContext) -> str:
    from odoo_instance_sdk.commands.context import project_provenance

    return project_provenance(cli_context)


def _validated_env_path(environment: DevelopmentEnvironment) -> str:
    """Return the registered active worktree only when it is usable."""
    if str(environment.state) != EnvironmentState.READY.value:
        raise RuntimeError(
            f"Environment {environment.name} is not active (state={environment.state})"
        )
    raw_path = environment.worktree_path
    if not isinstance(raw_path, str) or not raw_path or raw_path != raw_path.strip():
        raise RuntimeError("environment has no valid absolute worktree path")
    worktree = Path(raw_path)
    if not worktree.is_absolute():
        raise RuntimeError("environment worktree path is not absolute")
    try:
        is_directory = worktree.is_dir()
    except OSError as exc:
        raise RuntimeError("environment worktree is unavailable") from exc
    if not is_directory:
        raise RuntimeError("environment worktree is missing or not a directory")
    return raw_path


@env_group.command(
    "path",
    help='Print an active environment worktree path. Example: cd "$(odcli env path <environment>)".',
)
@click.argument("environment", required=False, metavar="ENVIRONMENT")
@output_options
@pass_cli_context
def env_path(
    ctx: CliContext,
    environment: str | None,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Print one validated absolute worktree path without changing state."""
    output_mode = resolve_output_mode(output_format, json_output)
    if environment is None and ctx.env is not None:
        fail(
            output_mode,
            "env.path",
            "root --env is not accepted by env path; pass ENVIRONMENT or cd into its worktree",
            dry_run=False,
            usage=True,
        )

    client = _client_class()(config=_client_config_class()(executable="odoo"))
    try:
        env_obj = resolve_environment(client, environment, cwd=Path.cwd())
        worktree_path = _validated_env_path(env_obj)
    except Exception as exc:
        fail(output_mode, "env.path", exc, dry_run=False)

    emit(
        success_document(
            command="env.path",
            result={
                "environment_id": str(env_obj.id),
                "name": env_obj.name,
                "worktree_path": worktree_path,
            },
            provenance={
                "environment_source": "cwd" if environment is None else "explicit",
            },
        ),
        output_mode,
        rich=lambda _document: worktree_path,
    )


def _render_env_list_rich(
    snapshot: Snapshot, worktree_paths: dict[str, str] | None = None
) -> Group:
    """Build the Rich inventory projection without collecting any data."""
    if worktree_paths is not None:
        for env in snapshot.environments:
            if env.id not in worktree_paths:
                raise RuntimeError(
                    "environment catalogue is missing a worktree path for an environment result"
                )
    paths = worktree_paths or {}
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
            table.add_column(column, overflow="fold", no_wrap=column == "WORKTREE")
        project_envs = sorted(envs_by_project.get(project.id, ()), key=lambda item: item.id)
        for env in project_envs:
            table.add_row(*_rich_env_row(env, paths.get(env.id)))
        sections.append(table)
    return Group(*sections)


def _rich_env_row(env: EnvironmentSnapshot, worktree_path: str | None = None) -> tuple[Text, ...]:
    """Return all sixteen environment values with terminal-aware styles."""
    values = _env_row_values(env, worktree_path)
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


def _env_row_values(env: EnvironmentSnapshot, worktree_path: str | None = None) -> tuple[str, ...]:
    if env.lifecycle_state is EnvironmentState.REMOVED:
        return _removed_env_row_values(env, worktree_path)
    return (
        env.name,
        env.branch,
        env.lifecycle_state.value,
        _runtime_str(env.runtime.state),
        _observed_str(env),
        _odoo_pid_str(env.runtime),
        f"{env.runtime.cpu_percent:.1f}%" if env.runtime.cpu_percent is not None else "—",
        _human_bytes(env.runtime.memory_bytes) if env.runtime.memory_bytes is not None else "—",
        _git_ahead_str(env.git),
        _git_diff_str(env.git),
        _size_str(env.storage),
        env.db_mode,
        env.database or "",
        _port_str(env),
        _artifacts_str(env.artifacts),
        worktree_path or "—",
    )


def _removed_env_row_values(
    env: EnvironmentSnapshot, worktree_path: str | None = None
) -> tuple[str, ...]:
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
        worktree_path or "—",
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


def _client_class() -> type[OdooClient]:
    return cast("type[OdooClient]", getattr(sys.modules[__name__], "OdooClient"))


def _client_config_class() -> type[OdooClientConfig]:
    return cast("type[OdooClientConfig]", getattr(sys.modules[__name__], "OdooClientConfig"))


def _monitor_class() -> type[EnvironmentMonitor]:
    return cast("type[EnvironmentMonitor]", getattr(sys.modules[__name__], "EnvironmentMonitor"))


def __getattr__(
    name: str,
) -> type[OdooClient | OdooClientConfig | EnvironmentMonitor | EnvironmentCheckoutOptions]:
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


@env_group.command("remove", aliases=["rm"], help="Remove an isolated development environment.")
@click.argument("environment", required=False)
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Skip confirmation.")
@output_options
@pass_cli_context
def env_remove(
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
                dry_run=dry_run,
                usage=True,
            )
        try:
            env_obj = resolve_environment(client, None)
        except Exception as e:
            fail(output_mode, "env.remove", str(e), dry_run=dry_run)
    else:
        try:
            resolve_project_path(ctx)
            env_obj = client.environments.get(environment)
        except Exception as e:
            fail(output_mode, "env.remove", str(e), dry_run=dry_run)
    try:
        command = client.environments.remove_command(env_obj)

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
                raise click.exceptions.Exit(0)  # noqa: TRY301

        status, _removed = run_or_preview(
            lambda: command,
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
                "project_source": _project_provenance(ctx),
                "environment_source": "explicit" if environment else "cwd",
            },
            rich=lambda _document: f"Removed environment {env_obj.name} ({env_obj.id})",
        )
        if dry_run:
            return
    except click.exceptions.Exit:
        raise
    except Exception as e:
        fail(output_mode, "env.remove", e, dry_run=dry_run)
    sys.exit(status)
    return


@env_group.command("sync", help="Synchronize an environment's Python dependencies.")
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
                dry_run=dry_run,
                usage=True,
            )
        try:
            environment = str(resolve_environment(client, None).id)
        except Exception as e:
            fail(output_mode, "env.sync", str(e), dry_run=dry_run)
    try:
        resolve_project_path(ctx)
        command = client.environments.sync_python_command(environment, upgrade=upgrade)
    except Exception as e:
        fail(output_mode, "env.sync", str(e), dry_run=dry_run)
    try:
        status, _result = run_or_preview(
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
            progress=True,
        )
    except Exception as exc:
        fail(output_mode, "env.sync", exc, dry_run=dry_run)
    raise click.exceptions.Exit(status)


def _env_dict(e: DevelopmentEnvironment | None) -> dict[str, JsonValue]:
    if e is None:
        return {}
    env = e
    return {
        "id": str(env.id),
        "name": env.name,
        "state": str(env.state),
        "branch": env.branch,
        "db_mode": str(env.db_mode),
        "http_port": env.http_port,
        "worktree_path": env.worktree_path,
    }


def _plan_lines(title: str, plan: dict[str, JsonValue]) -> list[str]:
    """Pure Rich projection for a structured domain/command plan."""
    return [
        title,
        *[
            f"{key}: {json.dumps(value, default=str, sort_keys=True)}"
            for key, value in plan.items()
        ],
    ]
