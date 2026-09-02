"""Project-level PostgreSQL lifecycle commands.

This module is intentionally startup-light: PostgreSQL resources and transport
helpers are resolved only after a callback starts building an operation.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click
import msgspec

from odoo_instance_sdk.commands.context import CliContext, pass_cli_context
from odoo_instance_sdk.commands.output import (
    OutputDocument,
    output_options,
    resolve_output_mode,
    run_or_preview,
)
from odoo_instance_sdk.models import PostgresClusterState

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import Command, JsonValue
    from odoo_instance_sdk.models import ClusterSnapshot
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
    return " ".join(parts)


@click.group()
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
@click.option("--dry-run", is_flag=True, default=False, help="Plan only.")
@output_options
@pass_cli_context
def postgres_status(
    ctx: CliContext, dry_run: bool, output_format: str | None, json_output: bool
) -> None:
    output_mode = resolve_output_mode(output_format, json_output)
    snapshot: ClusterSnapshot | None = None
    cluster_holder: dict[str, PostgresCluster] = {}

    def project_status(result: PostgresClusterState | None) -> dict[str, JsonValue]:
        nonlocal snapshot
        if result is None:
            return {}
        cluster = cluster_holder["cluster"]
        from odoo_instance_sdk.internal.postgres_cli import cluster_snapshot

        snapshot = cluster_snapshot(cluster, result)
        return cast("dict[str, JsonValue]", msgspec.to_builtins(snapshot))

    def build_command() -> Command[PostgresClusterState]:
        cluster = _postgres_cluster(ctx)
        cluster_holder["cluster"] = cluster
        return cluster.status_command()

    try:
        status, value = run_or_preview(
            build_command,
            command_name="postgres.status",
            mode=output_mode,
            dry_run=dry_run,
            result=project_status,
            rich=_cluster_rich,
        )
    except Exception as exc:
        from odoo_instance_sdk.commands.output import fail

        fail(output_mode, "postgres.status", exc)
    if value is not None and not dry_run and snapshot is not None:
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


__all__ = ["postgres_group"]
