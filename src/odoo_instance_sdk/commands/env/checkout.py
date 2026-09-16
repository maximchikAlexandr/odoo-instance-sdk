from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec

if TYPE_CHECKING:
    import click

    from odoo_instance_sdk.internal.proc import RunContext
else:
    import rich_click as click
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from odoo_instance_sdk.commands.context import (
    CliContext,
    pass_cli_context,
)
from odoo_instance_sdk.commands.env.deps import (
    _client_class,
    _client_config_class,
)
from odoo_instance_sdk.commands.env.display import (
    _ENV_LIST_COMPACT_COLUMNS,
    _ENV_LIST_MEDIUM_COLUMNS,
    _checkout_cluster_summary_line,
    _checkout_row_values,
    _checkout_status_style,
    _env_columns_for_width,
    _plan_lines,
    _provider_columns,
    _rich_checkout_compact_rows,
)
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputDocument,
    OutputMode,
    emit,
    emit_json_envelope,
    fail,
    field_schema,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
    sanitize_diagnostic,
    sanitize_terminal_text,
    success_document,
)
from odoo_instance_sdk.exceptions import BackupCatalogError, ProjectContextError, StalePlanError
from odoo_instance_sdk.internal.locks import exclusive_lock, provisioning_lock_path
from odoo_instance_sdk.internal.paths import get_catalog_path
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import (
    CheckoutInventory,
    CheckoutRow,
    ClusterSnapshot,
    DevelopmentEnvironment,
    EnvironmentCheckoutPlan,
    EnvironmentCheckoutResult,
    EnvironmentDatabaseMode,
    EnvironmentSnapshot,
    EnvironmentState,
    ProjectSummary,
    Snapshot,
)
from odoo_instance_sdk.project import ProjectConfig
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.resources.environment import EnvironmentCheckoutOptions
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor, SnapshotSelection


def select_snapshot_environment(
    snapshot: Snapshot,
    selector: str | None = None,
    *,
    cwd: Path | None = None,
    worktree_paths: dict[str, str] | None = None,
) -> SnapshotSelection:
    """Select records without collecting another monitor snapshot."""
    from odoo_instance_sdk.resources.monitor import select_snapshot_environment as select

    return select(snapshot, selector, cwd=cwd, worktree_paths=worktree_paths)


class _EnvShowResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Typed payload for the focused environment inspection leaf."""

    generated_at: datetime
    environment: EnvironmentSnapshot
    project: ProjectSummary
    cluster: ClusterSnapshot | None


_TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-[1-9][0-9]*\Z")
_TICKET_EVIDENCE_LIMIT = 32


class _TicketType(click.ParamType[str]):
    name = "TICKET"

    def convert(self, value: str, param: click.Parameter | None, ctx: click.Context | None) -> str:
        ticket = str(value)
        if _TICKET_RE.fullmatch(ticket) is None:
            self.fail("expected ticket like PROJ-123", param, ctx)
        return ticket


_TICKET = _TicketType()


@dataclass(frozen=True, slots=True)
class _TicketAllocation:
    ticket: str
    branch: str
    repo_root: Path
    git_common_dir: Path
    base_ref: str
    local_heads: tuple[str, ...]
    catalogue_heads: tuple[str, ...]
    remote_heads: tuple[str, ...]


def _ticket_iteration(ticket: str, branch: str) -> int | None:
    if branch == ticket:
        return 0
    match = re.fullmatch(rf"{re.escape(ticket)}_([1-9][0-9]*)", branch)
    return int(match.group(1)) if match is not None else None


def _catalogue_branch_names(
    client: OdooClient, repo_root: Path, git_common_dir: Path
) -> tuple[str, ...]:
    catalog = client.get_catalog()
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


def _resolve_ticket_allocation(
    client: OdooClient,
    project_path: Path,
    ticket: str,
    base_ref_override: str | None,
) -> _TicketAllocation:
    import odoo_instance_sdk.commands.env as _env_commands

    repo_root = _env_commands.rev_parse_toplevel(project_path)
    git_common_dir = _env_commands.rev_parse_git_common_dir(repo_root)
    project = ProjectConfig.load(repo_root)
    base_ref = base_ref_override or project.default_base_ref or "HEAD"
    local_heads = tuple(sorted(set(_env_commands.local_branch_names(repo_root))))
    catalogue_heads = tuple(
        sorted(set(_env_commands._catalogue_branch_names(client, repo_root, git_common_dir)))
    )
    remote_heads = tuple(sorted(set(_env_commands.remote_branch_names(repo_root, ticket))))
    all_heads = (*local_heads, *catalogue_heads, *remote_heads)
    iterations = [
        iteration
        for branch in all_heads
        if (iteration := _ticket_iteration(ticket, branch)) is not None
    ]
    next_iteration = max(iterations, default=-1) + 1
    branch = ticket if next_iteration == 0 else f"{ticket}_{next_iteration}"
    return _TicketAllocation(
        ticket=ticket,
        branch=branch,
        repo_root=repo_root,
        git_common_dir=git_common_dir,
        base_ref=base_ref,
        local_heads=local_heads,
        catalogue_heads=catalogue_heads,
        remote_heads=remote_heads,
    )


def _revalidate_ticket_absence(
    client: OdooClient,
    allocation: _TicketAllocation,
    *,
    context: RunContext[DevelopmentEnvironment],
) -> None:
    # The checkout command captures these Git reads as named process steps.
    # Do not call the generic git helpers while its RunContext is active:
    # that would create an unplanned default ``process`` step.
    local_result = context.process("checkout.ticket.local-heads")
    remote_result = context.process("checkout.ticket.remote-heads")
    local_output = getattr(local_result, "stdout", "")
    remote_output = getattr(remote_result, "stdout", "")
    local_heads = {line.strip() for line in str(local_output or "").splitlines() if line.strip()}
    remote_heads = {
        line.split("\t", 1)[1].removeprefix("refs/heads/").strip()
        for line in str(remote_output or "").splitlines()
        if "\t" in line and line.split("\t", 1)[1].startswith("refs/heads/")
    }
    import odoo_instance_sdk.commands.env as _env_commands

    sources = (
        ("local", local_heads),
        (
            "catalogue",
            set(
                _env_commands._catalogue_branch_names(
                    client, allocation.repo_root, allocation.git_common_dir
                )
            ),
        ),
        ("origin", remote_heads),
    )
    for source, branches in sources:
        if allocation.branch in branches:
            raise StalePlanError(
                "Ticket branch allocation became stale",
                expected={"branch": allocation.branch, "absent": True},
                actual={"source": source, "branch": allocation.branch},
            )


def _bounded_ticket_heads(heads: tuple[str, ...]) -> JsonObject:
    return {
        "heads": list(heads[:_TICKET_EVIDENCE_LIMIT]),
        "total": len(heads),
        "truncated": len(heads) > _TICKET_EVIDENCE_LIMIT,
    }


def _ticket_provenance(allocation: _TicketAllocation) -> JsonObject:
    return {
        "ticket_allocation": {
            "ticket": allocation.ticket,
            "resolved_branch": allocation.branch,
            "base_ref": allocation.base_ref,
            "evidence": {
                "local": _bounded_ticket_heads(allocation.local_heads),
                "catalogue": _bounded_ticket_heads(allocation.catalogue_heads),
                "origin": _bounded_ticket_heads(allocation.remote_heads),
            },
        }
    }


def _ticket_rich_lines(document: OutputDocument) -> list[str]:
    if not isinstance(document.provenance, dict):
        return []
    ticket = document.provenance.get("ticket_allocation")
    if not isinstance(ticket, dict):
        return []
    lines = [
        f"Ticket {ticket.get('ticket')} -> {ticket.get('resolved_branch')} (base {ticket.get('base_ref')})"
    ]
    evidence = ticket.get("evidence")
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
                    f"Ticket {source}: [{rendered_heads}] "
                    f"({source_data.get('total')} captured head(s){suffix})"
                )
    return lines


def _ticket_checkout_command(
    client: OdooClient,
    project_path: Path,
    options: EnvironmentCheckoutOptions,
    allocation: _TicketAllocation,
) -> Command[DevelopmentEnvironment]:
    selected_options = msgspec.structs.replace(options, base_ref=allocation.base_ref)
    from odoo_instance_sdk.resources.environment import EnvironmentResource

    environments = client.environments
    if isinstance(environments, EnvironmentResource):
        return environments._checkout_command_with_branch_revalidation(
            project_path,
            allocation.branch,
            options=selected_options,
            branch_revalidator=lambda context: _revalidate_ticket_absence(
                client, allocation, context=context
            ),
        )
    return environments.checkout_command(project_path, allocation.branch, options=selected_options)


def _build_ticket_checkout_command(
    client: OdooClient,
    project_path: Path,
    ticket: str,
    base_ref: str | None,
    options: EnvironmentCheckoutOptions,
) -> tuple[Command[DevelopmentEnvironment], _TicketAllocation | None]:
    from odoo_instance_sdk.resources.environment import EnvironmentResource

    if not isinstance(client.environments, EnvironmentResource):
        return (
            client.environments.checkout_command(project_path, ticket, options=options),
            None,
        )
    with exclusive_lock(provisioning_lock_path()):
        allocation = _resolve_ticket_allocation(
            client, project_path, ticket, base_ref_override=base_ref
        )
        return _ticket_checkout_command(client, project_path, options, allocation), allocation


@click.group(help="Manage isolated development environments.")
def env_group() -> None:
    pass


@env_group.command(
    "create",
    aliases=["checkout"],
    help="Create an isolated environment from a Ticket branch.",
)
@click.argument("ticket", metavar="TICKET", type=_TICKET)
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
@click.option(
    "--hash-lock",
    "hash_lock",
    type=click.Path(),
    default=None,
    help="Audited requirements lock for owned hash-locked synchronization.",
)
@click.option(
    "--hash-lock-sha256",
    "hash_lock_sha256",
    default=None,
    help="Expected SHA-256 digest of --hash-lock.",
)
@click.option("--http-port", "http_port", type=int, default=None, help="HTTP port.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Show plan only.")
@output_options
@pass_cli_context
def env_checkout(
    cli_ctx: CliContext,
    ticket: str,
    base_ref: str | None,
    config_path: str | None,
    db_mode: str,
    source_database: str | None,
    target_database: str | None,
    odoo_bin: str | None,
    python: str | None,
    create_venv: bool,
    hash_lock: str | None,
    hash_lock_sha256: str | None,
    http_port: int | None,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    try:
        import odoo_instance_sdk.commands.env as _env_commands
        from odoo_instance_sdk.execution import ExecutionPlan
        from odoo_instance_sdk.resources.environment import (
            EnvironmentCheckoutOptions,
            _checkout_public_plan,
        )

        project_path = _env_commands.resolve_project_path(cli_ctx)
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
            hash_lock=Path(hash_lock) if hash_lock else None,
            hash_lock_sha256=hash_lock_sha256,
            http_port=http_port,
        )
        command, allocation = _env_commands._build_ticket_checkout_command(
            client, project_path, ticket, base_ref, options
        )
        plan = _checkout_public_plan(command)
        ticket_provenance = _ticket_provenance(allocation) if allocation is not None else None

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
            lines.extend(_ticket_rich_lines(document))
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
            provenance=ticket_provenance,
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
        lines.extend(_ticket_rich_lines(document))
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
                **(ticket_provenance or {}),
            },
            dry_run=dry_run,
        ),
        output_mode,
        rich=rich_projection,
    )


@env_group.command(
    "ls", aliases=["list"], help="List initialized environments and their runtime state."
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
@field_schema(CheckoutInventory)
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
        import odoo_instance_sdk.commands.env as _env_commands

        project_id = _resolve_monitor_project_id(ctx, all_projects)
        monitor = _env_commands._monitor_class()()
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
        inventory = monitor.checkout_inventory(
            project_id=project_id,
            include_removed=include_removed if not machine_output else False,
        )
    except Exception as e:
        fail(output_mode, "env.list", str(e), dry_run=False)

    if machine_output:
        emit_json_envelope(
            ok=True,
            command="env.list",
            result=model_to_dict(inventory),
            provenance={
                "project_source": "null" if all_projects else _project_provenance(ctx),
                "environment_source": "null",
            },
            mode=output_mode,
        )
        return

    _print_env_list_human(inventory)


@env_group.command("show", help="Show one environment, its project, and PostgreSQL runtime facts.")
@click.argument("environment", required=False, metavar="ENVIRONMENT")
@output_options
@field_schema(_EnvShowResult)
@pass_cli_context
def env_show(
    ctx: CliContext,
    environment: str | None,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Render one read-only environment view from one monitor snapshot."""
    mode = resolve_output_mode(output_format, json_output)
    if environment is None and ctx.env is not None:
        fail(
            mode,
            "env.show",
            "root --env is not accepted by env show; pass ENVIRONMENT",
            dry_run=False,
            usage=True,
        )
    try:
        import odoo_instance_sdk.commands.env as _env_commands

        monitor = _env_commands._monitor_class()()
        snapshot = monitor.snapshot()
        paths: dict[str, str] = (
            _env_commands._catalog_worktree_paths(monitor, include_removed=True)
            if environment is None
            else {}
        )
        selected = _env_commands.select_snapshot_environment(
            snapshot, environment, cwd=Path.cwd(), worktree_paths=paths
        )
        payload = _EnvShowResult(
            generated_at=snapshot.generated_at,
            environment=selected.environment,
            project=selected.project,
            cluster=selected.cluster,
        )
        emit(
            success_document(
                command="env.show",
                result=model_to_dict(payload),
                provenance={
                    "environment_source": "cwd" if environment is None else "explicit",
                    "project_source": "worktree" if environment is None else "null",
                },
            ),
            mode,
            rich=_rich_env_show,
        )
    except Exception as exc:
        fail(mode, "env.show", exc, dry_run=False)


def _rich_env_show(document: OutputDocument) -> str:
    if not document.ok:
        return document.error.message if document.error is not None else "operation failed"
    result = document.result if isinstance(document.result, dict) else {}
    environment = result.get("environment")
    project = result.get("project")
    cluster = result.get("cluster")
    lines: list[str] = []
    if isinstance(environment, dict):
        lines.append(
            f"Environment {environment.get('name')} ({environment.get('id')}) "
            f"state={environment.get('lifecycle_state')}"
        )
        lines.append(
            f"  branch={environment.get('branch')} database={environment.get('database') or '—'} "
            f"runtime={_nested_value(environment, 'runtime', 'state') or '—'}"
        )
    if isinstance(project, dict):
        lines.append(f"Project {project.get('name')} ({project.get('id')})")
    if isinstance(cluster, dict):
        lines.append(
            f"PostgreSQL state={cluster.get('state')} "
            f"metrics={'available' if cluster.get('metrics') is not None else 'unavailable'}"
        )
    else:
        lines.append("PostgreSQL unavailable")
    return sanitize_terminal_text("\n".join(lines), preserve_newlines=True)


def _nested_value(value: dict[str, JsonValue], parent: str, key: str) -> JsonValue | None:
    child = value.get(parent)
    return child.get(key) if isinstance(child, dict) else None


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
                inventory = monitor.checkout_inventory(
                    project_id=project_id,
                    include_removed=include_removed,
                )
                last_renderable = _render_env_list_rich(inventory, width=Console().width)
                live.update(last_renderable, refresh=True)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if last_renderable is None:
                    fail(OutputMode.RICH, "env.list", str(exc), dry_run=False)
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
    import odoo_instance_sdk.commands.env as _env_commands

    try:
        project_path = _env_commands.resolve_project_path(ctx)
    except ProjectContextError:
        return None

    repo_root = _env_commands.rev_parse_toplevel(project_path)
    git_common = _env_commands.rev_parse_git_common_dir(repo_root)
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
    from odoo_instance_sdk.internal.paths import resolve_environment_artifact_paths

    paths: dict[str, str] = {}
    for row in rows:
        artifacts = resolve_environment_artifact_paths(
            environment_id=str(row["id"]),
            repository_root=str(row["repository_root"]),
            git_common_dir=str(row["git_common_dir"]),
            python_environment_owned=bool(int(row["python_environment_owned"])),
            python_environment_path=str(row["python_environment_path"]),
        )
        paths[str(row["id"])] = str(artifacts.worktree_path)
    return paths


def _print_env_list_human(inventory: CheckoutInventory) -> None:
    console = Console()
    console.print(_render_env_list_rich(inventory, width=console.width))


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

    import odoo_instance_sdk.commands.env as _env_commands

    client = _client_class()(config=_client_config_class()(executable="odoo"))
    try:
        env_obj = _env_commands.resolve_environment(client, environment, cwd=Path.cwd())
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


def _render_env_list_rich(inventory: CheckoutInventory, *, width: int = 300) -> Group:
    """Build the Rich checkout inventory projection without collecting any data."""
    provider_columns = _provider_columns(inventory)
    rows_by_project: dict[str, list[CheckoutRow]] = {}
    project_names: dict[str, str] = {}
    for row in inventory.rows:
        rows_by_project.setdefault(row.project_id, []).append(row)
        if row.kind == "main":
            project_names[row.project_id] = row.name
    clusters = {cluster.project_id: cluster for cluster in inventory.clusters}

    sections: list[Text | Table] = []
    for project_id in sorted(rows_by_project):
        project_name = project_names.get(project_id, project_id)
        sections.append(Text(sanitize_terminal_text(f"Project {project_name}"), style="bold cyan"))
        cluster = clusters.get(project_id)
        sections.append(
            Text(
                sanitize_terminal_text(
                    "  PostgreSQL  —"
                    if cluster is None
                    else _checkout_cluster_summary_line(cluster)
                ),
                style="dim",
            )
        )
        project_rows = rows_by_project[project_id]
        base_columns = _env_columns_for_width(width)
        columns = (*base_columns, *provider_columns)
        if base_columns in {_ENV_LIST_COMPACT_COLUMNS, _ENV_LIST_MEDIUM_COLUMNS}:
            sections.extend(
                _rich_checkout_compact_rows(
                    project_rows,
                    provider_columns=provider_columns,
                    include_worktree=width >= 120,
                )
            )
            continue
        table = Table(show_header=True, box=None, pad_edge=False)
        for column in columns:
            table.add_column(column, overflow="fold", no_wrap=column == "WORKTREE")
        for row in project_rows:
            values = _checkout_row_values(row, provider_columns)
            table.add_row(
                *(
                    Text(sanitize_terminal_text(values[column]), style=_checkout_status_style(row))
                    if column == "STATUS"
                    else Text(sanitize_terminal_text(values[column]))
                    for column in columns
                )
            )
        sections.append(table)
    return Group(*sections)
