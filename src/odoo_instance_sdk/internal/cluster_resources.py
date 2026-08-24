from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from odoo_instance_sdk.internal.postgres_compose import ComposeRunner, compose_ps, docker_available
from odoo_instance_sdk.models import (
    ClusterContainer,
    ClusterMetrics,
    ClusterResourceSnapshot,
    PidScope,
    PostgresClusterState,
)

_MEM_UNITS: dict[str, int] = {
    "b": 1,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
}

_MEM_RE = re.compile(r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]*)\s*$")


def _pid_scope() -> PidScope:
    # ponytail: platform split only; Colima/Desktop both run containers in a VM on darwin.
    return PidScope.DOCKER_VM if sys.platform == "darwin" else PidScope.HOST


def _parse_mem_value(s: str) -> int | None:
    m = _MEM_RE.match(s)
    if m is None:
        return None
    try:
        num = float(m.group("num"))
    except ValueError:
        return None
    unit = m.group("unit").lower()
    if unit == "":
        return int(num)
    factor = _MEM_UNITS.get(unit)
    if factor is None:
        return None
    return int(num * factor)


def _parse_cpu_percent(s: str) -> float | None:
    val = s.strip()
    if val.endswith("%"):
        val = val[:-1].strip()
    try:
        return float(val)
    except ValueError:
        return None


def resolve_container_id(
    compose_file: Path,
    compose_project_name: str,
    service: str,
    *,
    runner: ComposeRunner,
    timeout: float | None = None,
) -> str | None:
    """Resolve the full container ID for a compose service via `docker compose ps`.

    Returns the full container ID (Docker accepts both short and full for
    inspect/stats), or None if the service row is absent or the CLI fails.
    """
    rows = compose_ps(runner, compose_file, compose_project_name, timeout=timeout)
    if rows is None:
        return None
    for row in rows:
        if str(row.get("Service", "")) == service:
            cid = row.get("ID") or row.get("Id")
            if isinstance(cid, str) and cid:
                return cid
    return None


def _parse_inspect_payload(stdout: str, candidates: list[str]) -> dict[str, dict[str, object]]:
    parsed: dict[str, dict[str, object]] = {}
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return parsed
    if not isinstance(payload, list):
        return parsed
    for entry in payload:
        if isinstance(entry, dict) and isinstance(entry.get("Name"), str):
            parsed[_lookup_id(entry, candidates)] = entry
    return parsed


def inspect_containers(
    container_ids: tuple[str, ...],
    *,
    runner: ComposeRunner,
    timeout: float | None = None,
) -> dict[str, dict[str, object] | None]:
    """Batch `docker inspect <id1> <id2> ...` in one read-only CLI call."""
    res = _safe_run(
        runner, ["docker", "inspect", "--format", "json", *container_ids], timeout=timeout
    )
    # Docker returns a non-zero status when one requested ID is absent, while
    # still writing valid JSON for its healthy siblings.  The payload is the
    # authoritative per-container result; a non-zero status is not global
    # failure.  Malformed/non-list output deliberately remains a failure.
    parsed = _parse_inspect_payload(res.stdout, list(container_ids)) if res is not None else {}
    return {cid: parsed.get(cid) for cid in container_ids}


def _parse_stats_payload(stdout: str) -> dict[str, dict[str, object]]:
    by_id: dict[str, dict[str, object]] = {}
    for raw in stdout.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        ident = obj.get("container") or obj.get("Container") or obj.get("container_id")
        if isinstance(ident, str):
            by_id[ident] = obj
            by_id[ident[:12]] = obj
    return by_id


def stats_containers(
    container_ids: tuple[str, ...],
    *,
    runner: ComposeRunner,
    timeout: float | None = None,
) -> dict[str, dict[str, object] | None]:
    """Batch `docker stats --no-stream --format json <id1> <id2> ...`.

    One JSON object per line, identified by its `container`/`Container` field.
    """
    res = _safe_run(
        runner,
        ["docker", "stats", "--no-stream", "--format", "json", *container_ids],
        timeout=timeout,
    )
    # Like inspect, stats can contain useful lines before reporting a missing
    # container.  Preserve those samples and leave only absent IDs as None.
    by_id = _parse_stats_payload(res.stdout) if res is not None else {}
    return {cid: by_id.get(cid) or by_id.get(cid[:12]) for cid in container_ids}


def _lookup_id(entry: dict[str, object], candidates: list[str]) -> str:
    """Match an inspect entry back to one of the requested container IDs."""
    ident = entry.get("Id")
    if isinstance(ident, str):
        for cand in candidates:
            if cand == ident or ident.startswith(cand) or cand.startswith(ident):
                return cand
    name = entry.get("Name")
    if isinstance(name, str):
        for cand in candidates:
            if cand in name:
                return cand
    return candidates[0] if candidates else ""


def _safe_run(
    runner: ComposeRunner,
    args: Sequence[str],
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        result: subprocess.CompletedProcess[str] = runner.run(args, cwd=None, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return result


def cluster_resource_snapshot(
    *,
    compose_file: Path,
    compose_project_name: str,
    service: str,
    runner: ComposeRunner,
    state: PostgresClusterState,
    timeout: float | None = None,
) -> ClusterResourceSnapshot:
    """Build a read-only `ClusterResourceSnapshot` for one compose service.

    No lifecycle lock, no start/stop. The caller passes the already-computed
    cluster state so we can distinguish `stopped` from `missing` without a second
    status call. The monitor owns instance-level caching; this is the non-caching
    core shared with batch collection. This standalone helper has no TTL cache.
    """
    requires_docker = bool(getattr(runner, "requires_docker", True))
    if requires_docker and not docker_available():
        return ClusterResourceSnapshot(
            container=None,
            metrics=None,
            unavailability_reason="docker_unavailable",
            sampled_at=None,
        )

    if state is PostgresClusterState.STOPPED:
        return ClusterResourceSnapshot(
            container=None,
            metrics=None,
            unavailability_reason="stopped",
            sampled_at=None,
        )

    # Keep the public single-resource operation behaviorally identical to the
    # monitor's batch path; only the monitor owns TTL cache policy.
    return collect_cluster_resource_batch(
        (
            BatchClusterRequest(
                project_id="single",
                compose_file=compose_file,
                compose_project_name=compose_project_name,
                service=service,
                runner=runner,
                state=state,
            ),
        ),
        timeout=timeout,
    ).resources["single"]


@dataclass(frozen=True, slots=True)
class BatchClusterRequest:
    """One compose cluster request owned by a monitor collection pass."""

    project_id: str
    compose_file: Path
    compose_project_name: str
    service: str
    runner: ComposeRunner
    state: PostgresClusterState
    # Cache policy belongs to EnvironmentMonitor.  Only its persistent
    # production runner is cacheable; injected runners are isolated seams.
    cacheable: bool = False


@dataclass(frozen=True, slots=True)
class BatchClusterResult:
    """Assembled resources plus the Docker identities seen in this pass."""

    resources: dict[str, ClusterResourceSnapshot]
    container_ids: dict[str, str]


def collect_cluster_resource_batch(
    requests: Sequence[BatchClusterRequest],
    *,
    cached: dict[str, ClusterResourceSnapshot] | None = None,
    timeout: float | None = None,
) -> BatchClusterResult:
    """Resolve and collect compose resources, batching only compatible runners.

    Docker IDs are global, but injected runners are an explicit process boundary:
    their results must never be executed through another project's runner.
    ``cached`` is supplied by the monitor, which remains cache owner.
    """
    resources: dict[str, ClusterResourceSnapshot] = {}
    container_ids: dict[str, str] = {}
    pending: list[list[tuple[BatchClusterRequest, str]]] = []
    for request in requests:
        if request.state is PostgresClusterState.STOPPED:
            resources[request.project_id] = ClusterResourceSnapshot(
                container=None, metrics=None, unavailability_reason="stopped", sampled_at=None
            )
            continue
        container_id = resolve_container_id(
            request.compose_file,
            request.compose_project_name,
            request.service,
            runner=request.runner,
            timeout=timeout,
        )
        if container_id is None:
            resources[request.project_id] = ClusterResourceSnapshot(
                container=None, metrics=None, unavailability_reason="missing", sampled_at=None
            )
            continue
        container_ids[request.project_id] = container_id
        if request.cacheable and cached is not None and container_id in cached:
            resources[request.project_id] = cached[container_id]
            continue
        for group in pending:
            if group[0][0].runner is request.runner:
                group.append((request, container_id))
                break
        else:
            pending.append([(request, container_id)])

    for group in pending:
        runner = group[0][0].runner
        ids = tuple(dict.fromkeys(container_id for _, container_id in group))
        inspected = inspect_containers(ids, runner=runner, timeout=timeout)
        inspectable = tuple(cid for cid in ids if inspected.get(cid) is not None)
        stats = stats_containers(inspectable, runner=runner, timeout=timeout) if inspectable else {}
        sampled_at = datetime.now(UTC)
        for request, container_id in group:
            inspect_entry = inspected.get(container_id)
            if inspect_entry is None:
                resources[request.project_id] = ClusterResourceSnapshot(
                    container=None,
                    metrics=None,
                    unavailability_reason="inspect_failed",
                    sampled_at=None,
                )
                continue
            container = build_container(inspect_entry)
            stat = stats.get(container_id)
            resources[request.project_id] = (
                ClusterResourceSnapshot(
                    container=container,
                    metrics=build_metrics(stat, sampled_at),
                    unavailability_reason=None,
                    sampled_at=sampled_at,
                )
                if stat is not None
                else ClusterResourceSnapshot(
                    container=container,
                    metrics=None,
                    unavailability_reason="stats_failed",
                    sampled_at=None,
                )
            )
    return BatchClusterResult(resources=resources, container_ids=container_ids)


def build_container(inspect_entry: dict[str, object]) -> ClusterContainer:
    name_raw = inspect_entry.get("Name")
    name = name_raw[1:] if isinstance(name_raw, str) and name_raw.startswith("/") else name_raw
    config = inspect_entry.get("Config")
    image: str | None = None
    if isinstance(config, dict):
        img = config.get("Image")
        image = img if isinstance(img, str) else None
    state = inspect_entry.get("State")
    pid: int | None = None
    if isinstance(state, dict):
        raw_pid = state.get("Pid")
        if isinstance(raw_pid, int) and raw_pid > 0:
            pid = raw_pid
    ident = inspect_entry.get("Id")
    short = ident[:12] if isinstance(ident, str) else None
    pid_scope = _pid_scope() if pid is not None else PidScope.UNAVAILABLE
    return ClusterContainer(
        id=short,
        name=name if isinstance(name, str) else None,
        image=image,
        pid=pid,
        pid_scope=pid_scope,
    )


def build_metrics(stats_entry: dict[str, object], sampled_at: datetime) -> ClusterMetrics:
    cpu_raw = stats_entry.get("CPUPerc")
    cpu_percent: float | None = None
    if isinstance(cpu_raw, str):
        cpu_percent = _parse_cpu_percent(cpu_raw)

    mem_raw = stats_entry.get("MemUsage")
    memory_usage_bytes: int | None = None
    memory_limit_bytes: int | None = None
    if isinstance(mem_raw, str) and "/" in mem_raw:
        usage_str, limit_str = mem_raw.split("/", 1)
        memory_usage_bytes = _parse_mem_value(usage_str)
        memory_limit_bytes = _parse_mem_value(limit_str)

    # ponytail: volume usage via `docker system df -v` is heavy and platform-dependent; None is spec-allowed.
    return ClusterMetrics(
        cpu_percent=cpu_percent,
        memory_usage_bytes=memory_usage_bytes,
        memory_limit_bytes=memory_limit_bytes,
        volume_usage_bytes=None,
        sampled_at=sampled_at,
    )


__all__ = [
    "BatchClusterRequest",
    "BatchClusterResult",
    "build_container",
    "build_metrics",
    "cluster_resource_snapshot",
    "collect_cluster_resource_batch",
    "inspect_containers",
    "resolve_container_id",
    "stats_containers",
]
