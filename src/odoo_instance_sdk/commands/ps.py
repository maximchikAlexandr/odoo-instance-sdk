"""``odcli ps`` — read-only process/resource inventory leaf."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click
    from rich.table import Table
else:
    import rich_click as click
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.monitor_context import resolve_monitor_project_id
from odoo_instance_sdk.commands.output import (
    OutputMode,
    bordered_table,
    emit_json_envelope,
    fail,
    field_schema,
    model_to_dict,
    output_options,
    postgres_state_cells,
    resolve_output_mode,
    sanitize_diagnostic,
    sanitize_terminal_text,
)
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.models import (
    BackendProcessGroup,
    CheckoutProcessBlock,
    ClusterSnapshot,
    PidScope,
    ProcessContribution,
    ProcessGroupRuntime,
    ProcessInventory,
    SharedResourcesBlock,
)
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor


def _validate_watch_options(output_mode: OutputMode, *, watch: bool, interval: float) -> None:
    if interval < 0.1:
        raise click.UsageError("--interval must be at least 0.1 seconds")
    if not watch:
        return
    if output_mode is not OutputMode.RICH:
        raise click.UsageError("--watch is only available with Rich output")
    if not Console().is_terminal:
        fail(
            OutputMode.RICH,
            "ps",
            "--watch requires an interactive terminal",
            dry_run=False,
        )


@click.command(
    "ps",
    help="Show one read-only process and resource inventory from a single snapshot.",
)
@click.option(
    "--all-projects", "all_projects", is_flag=True, default=False, help="Inventory all projects."
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
@field_schema(ProcessInventory)
@pass_cli_context
def ps_command(
    ctx: CliContext,
    all_projects: bool,
    watch: bool,
    interval: float,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Render the read-only process/resource inventory."""
    output_mode = resolve_output_mode(output_format, json_output)
    _validate_watch_options(output_mode, watch=watch, interval=interval)
    try:
        project_id = resolve_monitor_project_id(ctx, all_projects)
        monitor = EnvironmentMonitor()
    except Exception as exc:
        fail(output_mode, "ps", str(exc), dry_run=False)

    if watch:
        try:
            _run_ps_live(monitor, project_id=project_id, interval=interval)
        except KeyboardInterrupt as exc:
            raise click.exceptions.Exit(130) from exc
        return

    try:
        inventory = monitor.processes_command(project_id=project_id).run()
    except Exception as exc:
        fail(output_mode, "ps", exc, dry_run=False)

    if output_mode is not OutputMode.RICH:
        emit_json_envelope(
            ok=True,
            command="ps",
            result=model_to_dict(inventory),
            provenance={
                "project_source": "null" if all_projects else "context",
                "environment_source": "null",
            },
            mode=output_mode,
        )
        return

    _print_ps_human(inventory)


def _run_ps_live(
    monitor: EnvironmentMonitor,
    *,
    project_id: str | None,
    interval: float,
) -> None:
    last_renderable: Group | None = None
    console = Console()
    with Live(None, transient=True) as live:
        while True:
            try:
                inventory = monitor.processes(project_id=project_id)
                last_renderable = _render_ps_rich(inventory, width=console.width)
                live.update(last_renderable, refresh=True)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if last_renderable is None:
                    fail(OutputMode.RICH, "ps", str(exc), dry_run=False)
                live.update(
                    Group(
                        last_renderable,
                        Text(f"Retrying: {sanitize_diagnostic(exc)}", style="yellow"),
                    ),
                    refresh=True,
                )
            time.sleep(interval)


def _print_ps_human(inventory: ProcessInventory) -> None:
    console = Console()
    console.print(_render_ps_rich(inventory, width=console.width))


def _render_ps_rich(inventory: ProcessInventory, *, width: int = 300) -> Group:
    sections: list[Text | Table] = []
    sections.append(Text("Process inventory", style="bold cyan"))
    for shared in inventory.shared:
        sections.extend(_shared_section(shared))
    if inventory.main_checkout is not None:
        sections.extend(_checkout_section(inventory.main_checkout, title="Main checkout"))
    for environment in inventory.environments:
        sections.extend(_checkout_section(environment, title=f"Environment {environment.name}"))
    return Group(*sections)


_PROCESS_COLUMNS = (
    "Type",
    "State",
    "PID / scope",
    "Processes",
    "CPU",
    "Memory",
    "Details",
)


def _process_table(rows: list[tuple[str, ...]]) -> Table:
    table = bordered_table(*_PROCESS_COLUMNS)
    for row in rows:
        table.add_row(
            *(Text(sanitize_terminal_text(value, preserve_newlines=True)) for value in row)
        )
    return table


def _empty_process_row(reason: str = "no proven process entry") -> tuple[str, ...]:
    return ("—", "unavailable", "unavailable", "—", "—", "—", reason)


def _runtime_row(runtime: ProcessGroupRuntime) -> tuple[str, ...]:
    pids = ((runtime.root_pid,) if runtime.root_pid is not None else ()) + runtime.child_pids
    scope = _pid_scope_cell(PidScope.HOST if pids else PidScope.UNAVAILABLE, pids)
    details = _details(
        _detail("database", runtime.database_name),
        _detail("endpoint", runtime.http_url),
        _detail("branch", runtime.branch),
        _detail("commit", runtime.commit_sha),
    )
    return (
        "odoo",
        runtime.state.value,
        scope,
        str(runtime.process_count),
        _cpu_cell(runtime.cpu_percent),
        _bytes_cell(runtime.memory_bytes),
        details,
    )


def _cluster_row(cluster: ClusterSnapshot) -> tuple[str, ...]:
    container = cluster.container
    if container is None:
        pid_scope = PidScope.UNAVAILABLE
        pids: tuple[int, ...] = ()
    elif container.pid is None:
        pid_scope = container.pid_scope
        pids = ()
    else:
        pid_scope = container.pid_scope
        pids = (container.pid,)

    state, reason = postgres_state_cells(
        cluster.state,
        cluster.unavailability_reason,
        cluster.server_unavailability_reason,
    )
    details = [f"ownership={'owned' if cluster.owned else 'unavailable'}"]
    if cluster.endpoint is not None:
        details.append(f"endpoint={cluster.endpoint.host}:{cluster.endpoint.port}")
    if cluster.server is not None:
        details.extend(
            (
                f"version={cluster.server.version}",
                f"connections={cluster.server.connections_active}/{cluster.server.connections_total}",
            )
        )
    if container is not None:
        details.extend(
            item
            for item in (
                _detail("container", container.id or container.name),
                _detail("image", container.image),
            )
            if item is not None
        )
    if reason:
        details.append(f"availability={reason}")
    metrics = cluster.metrics if cluster.owned else None
    return (
        "postgres",
        state,
        _pid_scope_cell(pid_scope, pids),
        "—",
        _cpu_cell(metrics.cpu_percent if metrics is not None else None),
        _bytes_cell(metrics.memory_usage_bytes if metrics is not None else None),
        _details(*details),
    )


def _backend_row(group: BackendProcessGroup) -> tuple[str, ...]:
    sessions = tuple(f"pid={session.pid}" for session in group.sessions)
    identities = tuple(
        "/".join(
            value for value in (session.application_name, session.user_name, session.state) if value
        )
        for session in group.sessions
    )
    details = _details(
        f"database={group.database}",
        f"connections={group.connection_count}",
        f"attribution={group.reason}",
        _detail("identity", ", ".join(identities) if identities else None),
        _detail("pids", ", ".join(sessions) if sessions else None),
        _detail("availability", group.unavailability_reason),
    )
    return (
        "postgres backend",
        "available" if group.unavailability_reason is None else "unavailable",
        _pid_scope_cell(group.pid_scope, group.host_pids),
        str(group.connection_count),
        _cpu_cell(group.cpu_percent),
        _bytes_cell(group.memory_bytes),
        details,
    )


def _contribution_row(contribution: ProcessContribution) -> tuple[str, ...]:
    pids = ((contribution.root_pid,) if contribution.root_pid is not None else ()) + (
        contribution.child_pids
    )
    details = _details(
        f"source={contribution.source}",
        f"identity={contribution.local_identity}",
        f"owner={contribution.owner_kind}:{contribution.owner_id or '—'}",
        f"availability={contribution.availability}",
        _detail("reason", contribution.unavailability_reason),
    )
    return (
        f"external/{contribution.source}",
        contribution.lifecycle_state,
        _pid_scope_cell(contribution.pid_scope, pids),
        str(len(pids)) if pids else "—",
        _cpu_cell(contribution.cpu_percent),
        _bytes_cell(contribution.memory_bytes),
        details,
    )


def _details(*values: str | None) -> str:
    present = tuple(value for value in values if value)
    return "\n".join(present) if present else "—"


def _detail(name: str, value: str | None) -> str | None:
    return f"{name}={value}" if value is not None else None


def _pid_scope_cell(scope: PidScope, pids: tuple[int, ...]) -> str:
    label = scope.value
    if not pids:
        return label if scope is PidScope.UNAVAILABLE else f"{label}:—"
    return f"{label}:{', '.join(str(pid) for pid in pids)}"


def _shared_section(shared: SharedResourcesBlock) -> list[Text | Table]:
    parts: list[Text | Table] = []
    parts.append(Text(f"Shared resources ({shared.project_id})", style="bold magenta"))
    rows: list[tuple[str, ...]] = []
    if shared.postgres_container is not None:
        rows.append(_cluster_row(shared.postgres_container))
    rows.extend(_backend_row(group) for group in shared.backend_groups)
    rows.extend(_contribution_row(item) for item in shared.external_contributions)
    parts.append(_process_table(rows or [_empty_process_row()]))
    if shared.postgres_container is not None:
        metrics = shared.postgres_container.metrics
        if metrics is not None and metrics.volume_usage_bytes is not None:
            parts.append(
                Text(
                    f"  storage volume={_human_bytes(metrics.volume_usage_bytes)}",
                    style="dim",
                )
            )
    return parts


def _checkout_section(block: CheckoutProcessBlock, *, title: str) -> list[Text | Table]:
    parts: list[Text | Table] = []
    parts.append(Text(title, style="bold green"))
    rows = [_runtime_row(block.odoo)] if block.odoo is not None else []
    rows.extend(_backend_row(group) for group in block.backend_groups)
    rows.extend(_contribution_row(item) for item in block.external_contributions)
    parts.append(_process_table(rows or [_empty_process_row()]))
    if block.storage is not None:
        parts.append(
            Text(
                f"  storage total={_human_bytes(block.storage.total_bytes)} "
                f"complete={'yes' if block.storage.complete else 'partial'}",
                style="dim",
            )
        )
    return parts


def _cpu_cell(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "—"


def _bytes_cell(value: int | None) -> str:
    return _human_bytes(value) if value is not None else "—"


def register_ps_command(group: click.Group) -> None:
    group.add_command(ps_command)


__all__ = ["ps_command", "register_ps_command"]
