"""``odcli ps`` — read-only process/resource inventory leaf."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click
else:
    import rich_click as click
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.monitor_context import resolve_monitor_project_id
from odoo_instance_sdk.commands.output import (
    OutputMode,
    emit_json_envelope,
    fail,
    field_schema,
    model_to_dict,
    output_options,
    resolve_output_mode,
    sanitize_diagnostic,
)
from odoo_instance_sdk.internal.cli_format import human_bytes as _human_bytes
from odoo_instance_sdk.models import (
    CheckoutProcessBlock,
    ClusterSnapshot,
    ProcessContribution,
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


def _shared_section(shared: SharedResourcesBlock) -> list[Text | Table]:
    parts: list[Text | Table] = []
    parts.append(Text(f"Shared resources ({shared.project_id})", style="bold magenta"))
    container = shared.postgres_container
    if container is not None:
        parts.append(Text(_cluster_line(container), style="dim"))
    else:
        parts.append(Text("  PostgreSQL —", style="dim"))
    if shared.backend_groups:
        table = Table(show_header=True, box=None, pad_edge=False)
        table.add_column("Database")
        table.add_column("Reason")
        table.add_column("Conns")
        table.add_column("PID scope")
        table.add_column("CPU")
        table.add_column("RSS")
        for group in shared.backend_groups:
            table.add_row(
                group.database,
                group.reason,
                str(group.connection_count),
                group.pid_scope.value,
                _cpu_cell(group.cpu_percent),
                _bytes_cell(group.memory_bytes),
            )
        parts.append(table)
    if shared.external_contributions:
        parts.append(Text("  External contributions", style="dim"))
        parts.append(_contributions_table(shared.external_contributions))
    return parts


def _checkout_section(block: CheckoutProcessBlock, *, title: str) -> list[Text | Table]:
    parts: list[Text | Table] = []
    parts.append(Text(title, style="bold green"))
    odoo = block.odoo
    if odoo is not None:
        table = Table(show_header=True, box=None, pad_edge=False)
        table.add_column("Kind")
        table.add_column("State")
        table.add_column("Procs")
        table.add_column("PID")
        table.add_column("CPU")
        table.add_column("RSS")
        table.add_column("Database")
        table.add_row(
            "odoo",
            odoo.state.value,
            str(odoo.process_count),
            _pid_cell(odoo.root_pid, odoo.child_pids),
            _cpu_cell(odoo.cpu_percent),
            _bytes_cell(odoo.memory_bytes),
            odoo.database_name or "—",
        )
        parts.append(table)
    if block.backend_groups:
        parts.append(Text("  PostgreSQL backends", style="dim"))
        table = Table(show_header=True, box=None, pad_edge=False)
        table.add_column("Database")
        table.add_column("Reason")
        table.add_column("Conns")
        table.add_column("PID scope")
        table.add_column("CPU")
        table.add_column("RSS")
        for group in block.backend_groups:
            table.add_row(
                group.database,
                group.reason,
                str(group.connection_count),
                group.pid_scope.value,
                _cpu_cell(group.cpu_percent),
                _bytes_cell(group.memory_bytes),
            )
        parts.append(table)
    if block.external_contributions:
        parts.append(Text("  External contributions", style="dim"))
        parts.append(_contributions_table(block.external_contributions))
    if block.storage is not None:
        parts.append(
            Text(
                f"  storage total={_human_bytes(block.storage.total_bytes)} "
                f"complete={'yes' if block.storage.complete else 'partial'}",
                style="dim",
            )
        )
    return parts


def _contributions_table(
    contributions: tuple[ProcessContribution, ...],
) -> Table:
    table = Table(show_header=True, box=None, pad_edge=False)
    table.add_column("Source")
    table.add_column("State")
    table.add_column("PID")
    table.add_column("CPU")
    table.add_column("RSS")
    table.add_column("Availability")
    for contribution in contributions:
        table.add_row(
            contribution.source,
            contribution.lifecycle_state,
            _pid_cell(contribution.root_pid, contribution.child_pids),
            _cpu_cell(contribution.cpu_percent),
            _bytes_cell(contribution.memory_bytes),
            contribution.availability,
        )
    return table


def _cluster_line(cluster: ClusterSnapshot) -> str:
    parts = ["  PostgreSQL", cluster.state.value]
    if cluster.unavailability_reason and cluster.unavailability_reason not in {
        "external_not_owned"
    }:
        parts.append(cluster.unavailability_reason)
        return "  ".join(parts)
    container = cluster.container
    if container is not None and container.id is not None:
        parts.append(f"container={container.id[:12]}")
        if container.pid is not None:
            scope_prefix = "vm" if container.pid_scope.value == "docker_vm" else "host"
            parts.append(f"pid={scope_prefix}:{container.pid}")
    metrics = cluster.metrics
    if metrics is not None:
        if metrics.cpu_percent is not None:
            parts.append(f"cpu={metrics.cpu_percent:.1f}%")
        if metrics.memory_usage_bytes is not None:
            parts.append(f"ram={_human_bytes(metrics.memory_usage_bytes)}")
        if metrics.volume_usage_bytes is not None:
            parts.append(f"disk={_human_bytes(metrics.volume_usage_bytes)}")
    return "  ".join(parts)


def _cpu_cell(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "—"


def _bytes_cell(value: int | None) -> str:
    return _human_bytes(value) if value is not None else "—"


def _pid_cell(root_pid: int | None, child_pids: tuple[int, ...]) -> str:
    if root_pid is None:
        return "—"
    child = len(child_pids)
    return f"{root_pid} (+{child})" if child else str(root_pid)


def register_ps_command(group: click.Group) -> None:
    group.add_command(ps_command)


__all__ = ["ps_command", "register_ps_command"]
