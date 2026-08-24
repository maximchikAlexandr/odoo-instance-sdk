from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from typing import TypeVar

import click

from odoo_instance_sdk.exceptions import OdooInstanceSdkError
from odoo_instance_sdk.internal import context as cli_context
from odoo_instance_sdk.internal.cli_format import human_bytes
from odoo_instance_sdk.internal.cli_output import emit_json_envelope, fail
from odoo_instance_sdk.models import (
    ClusterContainer,
    ClusterEndpoint,
    ClusterMetrics,
    ClusterSnapshot,
    PostgresClusterState,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster

_PostgresCommandResult = TypeVar("_PostgresCommandResult")


def run_postgres_command(
    ctx: click.Context,
    *,
    command: str,
    json_output: bool,
    operation: Callable[[PostgresCluster], _PostgresCommandResult],
) -> tuple[PostgresCluster, _PostgresCommandResult]:
    """Resolve a cluster and run one postgres command under the CLI envelope."""
    try:
        cluster = PostgresCluster.from_project(cli_context.resolve_project_path(ctx))
        return cluster, operation(cluster)
    except SystemExit:
        raise
    except OdooInstanceSdkError as exc:
        fail(json_output, command, str(exc))
    except Exception as exc:
        fail(json_output, command, str(exc))
    raise AssertionError("fail always exits")


def emit_postgres_result(
    *, cluster: PostgresCluster, state: PostgresClusterState, command: str, json_output: bool
) -> None:
    diag = dict(cluster.to_diagnostic_dict())
    diag["state"] = state.value
    if json_output:
        emit_json_envelope(ok=True, command=command, result=diag)
    else:
        click.echo(
            f"mode={cluster.mode} owned={cluster.owned} state={state.value} endpoint={cluster.endpoint}"
        )


def cluster_snapshot(cluster: PostgresCluster, state: PostgresClusterState) -> ClusterSnapshot:
    """Build the canonical typed status object without a dict conversion boundary."""
    try:
        endpoint: ClusterEndpoint | None = ClusterEndpoint(
            host=cluster.endpoint_host, port=cluster.endpoint_port
        )
    except Exception:
        endpoint = None

    container: ClusterContainer | None = None
    metrics: ClusterMetrics | None = None
    reason: str | None = "external_not_owned" if not cluster.owned else None
    sampled_at = None
    if cluster.owned:
        resource = cluster.resource_snapshot()
        if resource is not None:
            container = resource.container
            metrics = resource.metrics
            reason = resource.unavailability_reason
            sampled_at = resource.sampled_at
    return ClusterSnapshot(
        mode=cluster.mode,
        owned=cluster.owned,
        state=state,
        endpoint=endpoint,
        container=container,
        metrics=metrics,
        unavailability_reason=reason,
        sampled_at=sampled_at,
    )


def status_exit_code(snapshot: ClusterSnapshot) -> int:
    """A diagnostic external cluster succeeds only when its TCP probe is healthy."""
    if snapshot.state is PostgresClusterState.HEALTHY:
        return 0
    if snapshot.owned and snapshot.unavailability_reason in {
        "stopped",
        "missing",
        "docker_unavailable",
    }:
        return 0
    return 1


def print_status(snapshot: ClusterSnapshot) -> None:
    """Render the typed status object in the compact human CLI format."""
    endpoint = (
        f"{snapshot.endpoint.host}:{snapshot.endpoint.port}"
        if snapshot.endpoint is not None
        else "—"
    )
    parts = [
        f"mode={snapshot.mode}",
        f"owned={snapshot.owned}",
        f"state={snapshot.state.value}",
        f"endpoint={endpoint}",
    ]
    if snapshot.unavailability_reason not in {None, "external_not_owned"}:
        parts.append(f"reason={snapshot.unavailability_reason}")
    if snapshot.container is not None:
        container = snapshot.container
        parts.append(f"container={container.id[:12] if container.id else 'missing'}")
        if container.name:
            parts.append(f"name={container.name}")
        if container.image:
            parts.append(f"image={container.image}")
        if container.pid is not None and container.pid_scope is not None:
            prefix = "vm" if container.pid_scope.value == "docker_vm" else "host"
            parts.append(f"pid={prefix}:{container.pid}")
    if snapshot.metrics is not None:
        metrics = snapshot.metrics
        if metrics.cpu_percent is not None:
            parts.append(f"cpu={metrics.cpu_percent:.1f}%")
        if metrics.memory_usage_bytes is not None:
            parts.append(f"ram={human_bytes(metrics.memory_usage_bytes)}")
        if metrics.volume_usage_bytes is not None:
            parts.append(f"disk={human_bytes(metrics.volume_usage_bytes)}")
    click.echo("  ".join(parts))


def run_psql(
    *,
    host: str | None,
    port: int,
    user: str | None,
    password: str | None,
    query: str,
    timeout: float,
) -> subprocess.CompletedProcess[str] | None:
    """Run one read-only psql query with a scrubbed password environment."""
    if user is None or shutil.which("psql") is None:
        return None
    env = os.environ.copy()
    # Do not accidentally inherit credentials from the agent/CI process.
    env.pop("PGPASSWORD", None)
    if password is not None:
        env["PGPASSWORD"] = password
    cmd = ["psql"]
    if host is not None:
        cmd.extend(["-h", host])
    cmd.extend(["-p", str(port), "-U", user, "-d", "postgres", "-t", "-A", "-c", query])
    try:
        return subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=timeout, shell=False, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
