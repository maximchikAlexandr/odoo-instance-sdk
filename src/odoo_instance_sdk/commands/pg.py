"""Project-level PostgreSQL lifecycle commands.

This module is intentionally startup-light: PostgreSQL resources and transport
helpers are resolved only after a callback starts building an operation.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click
import msgspec

from odoo_instance_sdk.commands.context import (
    CliContext,
    environment_provenance,
    pass_cli_context,
)
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    OutputMode,
    fail,
    model_to_dict,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.models import PostgresClusterState

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.internal.pg.server import ServerSummary
    from odoo_instance_sdk.models import ClusterSnapshot, DevelopmentEnvironment
    from odoo_instance_sdk.resources.database import DatabaseResource
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster


def _postgres_cluster(ctx: CliContext) -> PostgresCluster:
    """Resolve PostgreSQL resources only while composing a command."""
    from odoo_instance_sdk.internal import postgres_cli
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    resolve_project_path = cast(
        "Callable[[CliContext], str | Path]", getattr(postgres_cli, "resolve_project_path")
    )
    return PostgresCluster.from_project(resolve_project_path(ctx))


def _cluster_rich(document: OutputDocument) -> str:
    payload = document.result if isinstance(document.result, dict) else {}
    endpoint = payload.get("endpoint", "—")
    parts = [
        f"mode={payload.get('mode', 'unknown')} owned={payload.get('owned', False)} "
        f"state={payload.get('state', 'unknown')} endpoint={endpoint}"
    ]
    container = payload.get("container")
    if isinstance(container, dict):
        if container.get("id"):
            parts.append(f"container={str(container['id'])[:12]}")
        if container.get("pid") is not None:
            scope = "vm" if container.get("pid_scope") == "docker_vm" else "host"
            parts.append(f"pid={scope}:{container['pid']}")
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        cpu = metrics.get("cpu_percent")
        memory = metrics.get("memory_usage_bytes")
        volume = metrics.get("volume_usage_bytes")
        if isinstance(cpu, (int, float)):
            parts.append(f"cpu={float(cpu):.1f}%")
        if isinstance(memory, (int, float)):
            parts.append(f"ram={int(memory) / 1024**2:.1f} MiB")
        if isinstance(volume, (int, float)):
            parts.append(f"disk={int(volume) / 1024**3:.1f} GiB")
    server = payload.get("server")
    if isinstance(server, dict):
        parts.append(
            "server="
            f"{server.get('version', 'unknown')} "
            f"uptime={server.get('uptime_seconds', 0)}s "
            f"connections={server.get('connections_active', 0)}/"
            f"{server.get('connections_total', 0)}"
        )
    elif payload.get("server_unavailability_reason") is not None:
        parts.append(f"server={payload['server_unavailability_reason']}")
    return " ".join(parts)


def _database_instance(ctx: CliContext) -> tuple[DevelopmentEnvironment | None, OdooInstance]:
    """Resolve a bound database instance without requiring Odoo to be ready.

    The generated configuration remains the source of database identity when
    an environment is stopped.  ``from_environment`` is retained for ready
    environments because it also restores the recorded runtime command
    prefix; stopped environments only need the database resource and the
    project PostgreSQL cluster for shared lifecycle preflight.
    """
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.commands.context import resolve_environment, resolve_project_path
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.exceptions import EnvironmentResolutionError
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.environment import EnvironmentState
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    project_root = resolve_project_path(ctx).resolve()
    try:
        environment = resolve_environment(client, ctx.env, cwd=Path.cwd())
    except EnvironmentResolutionError:
        if ctx.env is not None:
            raise
        project = ProjectConfig.load(project_root)
        if project.source_config is None:
            raise
        instance = client.instance.from_config(project.source_config)
        instance._postgres_cluster = PostgresCluster.from_project(project_root)
        return None, instance
    if Path(environment.repository_root).resolve() != project_root:
        raise RuntimeError(
            f"Environment {environment.name} ({environment.id}) does not belong to project "
            f"{project_root}"
        )
    config_path = Path(environment.generated_config_path)
    if not config_path.is_file():
        raise RuntimeError(f"generated config missing: {config_path}")
    if environment.state is EnvironmentState.READY:
        instance = client.instance.from_environment(environment)
    else:
        instance = client.instance.from_config(config_path)
        instance._postgres_cluster = PostgresCluster.from_project(Path(environment.repository_root))
        instance._environment_id = str(environment.id)
    return environment, instance


def _database_resource(
    ctx: CliContext, database: str | None
) -> tuple[DevelopmentEnvironment | None, DatabaseResource, str]:
    from odoo_instance_sdk.internal.pg.context import resolve_database_context

    environment, instance = _database_instance(ctx)
    binding = resolve_database_context(instance, explicit=database)
    return environment, instance.databases, binding.database


def _render_rows(title: str, rows: JsonValue) -> str:
    """Render one typed row collection as a Rich table without changing data."""
    from rich.console import Console
    from rich.table import Table

    output = StringIO()
    console = Console(file=output, color_system=None, force_terminal=False, width=120)
    table = Table(title=title)
    if not isinstance(rows, (list, tuple)) or not rows:
        table.add_column("value")
        table.add_row("(none)")
    else:
        first = rows[0]
        payload = first
        if not isinstance(payload, dict):
            table.add_column("value")
            table.add_row(str(payload))
        else:
            columns = tuple(str(key) for key in payload)
            for column in columns:
                table.add_column(column)
            for row in rows:
                value = row
                if isinstance(value, dict):
                    table.add_row(*(str(value.get(column, "")) for column in columns))
    console.print(table)
    return output.getvalue().rstrip()


def _locks_rich(document: OutputDocument) -> str:
    payload = document.result if isinstance(document.result, dict) else {}
    return _render_rows("Locks", payload.get("rows", []))


def _stats_rich(document: OutputDocument) -> str:
    payload = document.result if isinstance(document.result, dict) else {}
    return "\n".join(
        (
            _render_rows("Tables", payload.get("tables", [])),
            _render_rows("Indexes", payload.get("indexes", [])),
        )
    )


def _bloat_rich(document: OutputDocument) -> str:
    payload = document.result if isinstance(document.result, dict) else {}
    return "\n".join(
        (
            _render_rows("Tables", payload.get("tables", [])),
            _render_rows("Indexes", payload.get("indexes", [])),
        )
    )


def _monitoring_rich(document: OutputDocument) -> str:
    payload = document.result if isinstance(document.result, dict) else {}
    return "installed={installed} already_present={already} skipped={skipped}".format(
        installed=payload.get("installed", ()),
        already=payload.get("already_present", ()),
        skipped=payload.get("skipped", ()),
    )


def _run_database_command(
    ctx: CliContext,
    database: str | None,
    *,
    command_name: str,
    output_mode: OutputMode,
    dry_run: bool,
    build: Callable[[DatabaseResource, str], Command[msgspec.Struct]],
    rich: Callable[[OutputDocument], str],
) -> None:
    try:
        context: dict[str, JsonValue] = {}

        def build_command() -> Command[msgspec.Struct]:
            _environment, resource, resolved = _database_resource(ctx, database)
            context["database"] = resolved
            return build(resource, resolved)

        status, _value = run_or_preview(
            build_command,
            command_name=command_name,
            mode=output_mode,
            dry_run=dry_run,
            result=cast("Callable[[msgspec.Struct | None], dict[str, JsonValue]]", model_to_dict),
            context=context,
            provenance={"environment_source": environment_provenance(ctx)},
            rich=rich,
        )
    except Exception as exc:
        fail(output_mode, command_name, exc)
    raise click.exceptions.Exit(status)


@click.command(
    "psql",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("psql_args", nargs=-1, type=click.UNPROCESSED)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@pass_cli_context
def psql(
    ctx: CliContext,
    psql_args: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Run the bound native psql client with inherited terminal streams."""
    for argument in psql_args:
        if argument == "--json" or argument == "--format" or argument.startswith("--format="):
            raise click.UsageError(f"No such option: {argument}")
    try:
        output_mode = resolve_output_mode(None, False)
        _, resource, _database = _database_resource(ctx, None)
        status, value = run_or_preview(
            lambda: resource.psql_command(psql_args),
            command_name="psql",
            mode=output_mode,
            dry_run=dry_run,
            emit_normal=False,
        )
    except Exception as exc:
        fail(output_mode, "psql", exc)
    raise click.exceptions.Exit(status if value is None else value)


@click.command("locks")
@click.argument("database", required=False)
@click.option("--top", type=click.IntRange(min=1, max=1000), default=20, show_default=True)
@click.option("--timeout", type=click.FloatRange(min=0.001), default=30.0, show_default=True)
@output_options
@pass_cli_context
def db_locks(
    ctx: CliContext,
    database: str | None,
    top: int,
    timeout: float,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Show active PostgreSQL lock blockers."""
    _run_database_command(
        ctx,
        database,
        command_name="db.locks",
        output_mode=resolve_output_mode(output_format, json_output),
        dry_run=False,
        build=lambda resource, resolved: cast(
            "Command[msgspec.Struct]", resource.locks_command(resolved, top=top, timeout=timeout)
        ),
        rich=_locks_rich,
    )


@click.command("stats")
@click.argument("database", required=False)
@click.option("--top", type=click.IntRange(min=1, max=1000), default=20, show_default=True)
@click.option("--timeout", type=click.FloatRange(min=0.001), default=30.0, show_default=True)
@output_options
@pass_cli_context
def db_stats(
    ctx: CliContext,
    database: str | None,
    top: int,
    timeout: float,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Show PostgreSQL table and index statistics."""
    _run_database_command(
        ctx,
        database,
        command_name="db.stats",
        output_mode=resolve_output_mode(output_format, json_output),
        dry_run=False,
        build=lambda resource, resolved: cast(
            "Command[msgspec.Struct]", resource.stats_command(resolved, top=top, timeout=timeout)
        ),
        rich=_stats_rich,
    )


@click.command("bloat")
@click.argument("database", required=False)
@click.option("--top", type=click.IntRange(min=1, max=1000), default=20, show_default=True)
@click.option(
    "--exact-max-scan-mb", type=click.IntRange(min=0, max=1024), default=64, show_default=True
)
@click.option("--timeout", type=click.FloatRange(min=0.001), default=30.0, show_default=True)
@output_options
@pass_cli_context
def db_bloat(
    ctx: CliContext,
    database: str | None,
    top: int,
    exact_max_scan_mb: int,
    timeout: float,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Show estimated and bounded exact PostgreSQL bloat."""
    _run_database_command(
        ctx,
        database,
        command_name="db.bloat",
        output_mode=resolve_output_mode(output_format, json_output),
        dry_run=False,
        build=lambda resource, resolved: cast(
            "Command[msgspec.Struct]",
            resource.bloat_command(
                resolved, top=top, exact_max_scan_mb=exact_max_scan_mb, timeout=timeout
            ),
        ),
        rich=_bloat_rich,
    )


@click.command("init-monitoring")
@click.argument("database", required=False)
@click.option("--yes", is_flag=True, default=False, help="Confirm extension installation.")
@click.option("--timeout", type=click.FloatRange(min=0.001), default=30.0, show_default=True)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def db_init_monitoring(
    ctx: CliContext,
    database: str | None,
    yes: bool,
    timeout: float,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    """Install the explicitly supported PostgreSQL monitoring extensions."""
    output_mode = resolve_output_mode(output_format, json_output)
    if not yes and not dry_run and output_mode is not OutputMode.RICH:
        fail(
            output_mode,
            "db.init-monitoring",
            "init-monitoring requires --yes",
            error_code="confirmation_required",
        )
    confirm = None
    if not yes and not dry_run:

        def confirm() -> None:
            click.confirm("Install PostgreSQL monitoring extensions?", abort=True)

    try:
        context: dict[str, JsonValue] = {}

        def build_command() -> Command[msgspec.Struct]:
            _environment, resource, resolved = _database_resource(ctx, database)
            context["database"] = resolved
            return cast(
                "Command[msgspec.Struct]",
                resource.init_monitoring_command(resolved, timeout=timeout),
            )

        status, _value = run_or_preview(
            build_command,
            command_name="db.init-monitoring",
            mode=output_mode,
            dry_run=dry_run,
            result=cast("Callable[[msgspec.Struct | None], dict[str, JsonValue]]", model_to_dict),
            context=context,
            provenance={"environment_source": environment_provenance(ctx)},
            confirm=confirm,
            rich=_monitoring_rich,
        )
    except Exception as exc:
        fail(output_mode, "db.init-monitoring", exc)
    raise click.exceptions.Exit(status)


@click.group(help="Inspect and manage project PostgreSQL.")
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
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def postgres_approve_image(
    ctx: CliContext,
    image_digest: str,
    timeout: float,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    """Approve the current compose image in the local, non-repository trust store."""
    try:
        cluster_holder: dict[str, PostgresCluster] = {}

        def build_command() -> Command[None]:
            cluster = _postgres_cluster(ctx)
            cluster_holder["cluster"] = cluster
            return cluster.approve_image_command(image_digest, timeout=timeout)

        status, _value = run_or_preview(
            build_command,
            command_name="postgres.approve-image",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda result: {
                "approved": True,
                "image": cast("JsonValue", cluster_holder["cluster"].to_diagnostic_dict()["image"])
                if result is not None
                else None,
                "digest": image_digest,
            },
            rich=lambda document: (
                f"approved image={document.result.get('image')} digest={image_digest}"
                if isinstance(document.result, dict)
                else ""
            ),
        )
    except Exception as exc:
        from odoo_instance_sdk.commands.output import fail

        fail(output_mode, "postgres.approve-image", exc)
    sys.exit(status)


@postgres_group.command("status")
@output_options
@pass_cli_context
def postgres_status(ctx: CliContext, output_format: str | None, json_output: bool) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    snapshot: ClusterSnapshot | None = None
    cluster_holder: dict[str, PostgresCluster] = {}
    server_summary_holder: dict[str, ServerSummary] = {}

    def project_status(result: PostgresClusterState | None) -> dict[str, JsonValue]:
        nonlocal snapshot
        if result is None:
            return {}
        cluster = cluster_holder["cluster"]
        from odoo_instance_sdk.internal.postgres_cli import cluster_snapshot

        summary = server_summary_holder.get("summary")

        if summary is None:
            snapshot = cluster_snapshot(cluster, result)
        else:
            snapshot = cluster_snapshot(cluster, result, server_summary=summary)
        return cast("dict[str, JsonValue]", msgspec.to_builtins(snapshot))

    def build_command() -> Command[PostgresClusterState]:
        cluster = _postgres_cluster(ctx)
        cluster_holder["cluster"] = cluster
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        if isinstance(cluster, PostgresCluster):

            def receive_summary(summary: ServerSummary) -> None:
                server_summary_holder["summary"] = summary

            return cluster.status_command(server_summary_sink=receive_summary)
        return cluster.status_command()

    try:
        status, value = run_or_preview(
            build_command,
            command_name="postgres.status",
            mode=output_mode,
            dry_run=False,
            result=project_status,
            rich=_cluster_rich,
        )
    except Exception as exc:
        from odoo_instance_sdk.commands.output import fail

        fail(output_mode, "postgres.status", exc)
    if value is not None and snapshot is not None:
        from odoo_instance_sdk.internal.postgres_cli import status_exit_code

        sys.exit(status_exit_code(snapshot))
    sys.exit(status)


@postgres_group.command("up")
@click.option(
    "--wait-timeout",
    "wait_timeout",
    type=float,
    default=60.0,
    help="Seconds to wait for the cluster to become healthy.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def postgres_up(
    ctx: CliContext,
    wait_timeout: float,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    cluster_holder: dict[str, PostgresCluster] = {}

    def build_command() -> Command[None]:
        cluster = _postgres_cluster(ctx)
        cluster_holder["cluster"] = cluster
        return cluster.ensure_running_command(timeout=wait_timeout)

    try:
        status, _value = run_or_preview(
            build_command,
            command_name="postgres.up",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda result: cast(
                "dict[str, JsonValue]",
                cluster_holder["cluster"].to_diagnostic_dict() if result is not None else {},
            ),
            rich=_cluster_rich,
        )
    except Exception as exc:
        from odoo_instance_sdk.commands.output import fail

        fail(output_mode, "postgres.up", exc)
    sys.exit(status)


@postgres_group.command("stop")
@click.option(
    "--timeout",
    "timeout",
    type=float,
    default=30.0,
    help="Seconds to wait for graceful stop.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def postgres_stop(
    ctx: CliContext,
    timeout: float,
    dry_run: bool,
    output_format: str | None,
    json_output: bool,
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    cluster_holder: dict[str, PostgresCluster] = {}

    def build_command() -> Command[None]:
        cluster = _postgres_cluster(ctx)
        cluster_holder["cluster"] = cluster
        return cluster.stop_command(timeout=timeout)

    try:
        status, _value = run_or_preview(
            build_command,
            command_name="postgres.stop",
            mode=output_mode,
            dry_run=dry_run,
            result=lambda result: cast(
                "dict[str, JsonValue]",
                cluster_holder["cluster"].to_diagnostic_dict() if result is not None else {},
            ),
            rich=_cluster_rich,
        )
    except Exception as exc:
        from odoo_instance_sdk.commands.output import fail

        fail(output_mode, "postgres.stop", exc)
    sys.exit(status)


def register_database_commands(group: click.Group) -> None:
    """Attach PostgreSQL-specific leaves to the existing ``db`` group."""
    group.add_command(db_locks, name="locks")
    group.add_command(db_stats, name="stats")
    group.add_command(db_bloat, name="bloat")
    group.add_command(db_init_monitoring, name="init-monitoring")


__all__ = [
    "db_bloat",
    "db_init_monitoring",
    "db_locks",
    "db_stats",
    "postgres_group",
    "psql",
    "register_database_commands",
]
