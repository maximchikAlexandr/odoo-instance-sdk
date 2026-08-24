from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Sequence
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

# ponytail: in-memory, process-local caches keyed by container_id, TTL 15s.
_inspect_cache: dict[str, tuple[float, dict[str, object]]] = {}
_stats_cache: dict[str, tuple[float, dict[str, object]]] = {}
_CACHE_TTL = 15.0

_HEXDIGITS = "0123456789abcdef"

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


def _clear_caches() -> None:
    """Test hook: reset module-level caches."""
    _inspect_cache.clear()
    _stats_cache.clear()


def _short_id(container_id: str) -> str:
    short = container_id[:12]
    return short if all(c in _HEXDIGITS for c in short) else container_id[:12]


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


def _partition_cached(
    container_ids: tuple[str, ...],
    cache: dict[str, tuple[float, dict[str, object]]],
) -> tuple[dict[str, dict[str, object] | None], list[str]]:
    """Split requested IDs into cached results and pending IDs needing a fetch."""
    result: dict[str, dict[str, object] | None] = {}
    pending: list[str] = []
    now = time.monotonic()
    for cid in container_ids:
        cached = cache.get(cid)
        if cached is not None and now - cached[0] < _CACHE_TTL:
            result[cid] = cached[1]
        else:
            pending.append(cid)
    return result, pending


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
    """Batch `docker inspect <id1> <id2> ...`; one CLI call for uncached IDs.

    Returns a dict keyed by the *full* container_id; None marks a failed or
    missing entry. Cached entries (TTL 15s) are reused without a subprocess call.
    """
    result, pending = _partition_cached(container_ids, _inspect_cache)
    if not pending:
        return result
    res = _safe_run(runner, ["docker", "inspect", "--format", "json", *pending], timeout=timeout)
    parsed = (
        _parse_inspect_payload(res.stdout, pending)
        if res is not None and res.returncode == 0
        else {}
    )
    now = time.monotonic()
    for cid in pending:
        entry: dict[str, object] | None = parsed.get(cid)
        result[cid] = entry
        # ponytail: cache miss as empty sentinel so we don't refetch within TTL.
        _inspect_cache[cid] = (now, entry) if entry is not None else (now, {})
    return result


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

    One JSON object per line, identified by its `container`/`Container` field
    (full or short ID). Cached entries (TTL 15s) are reused.
    """
    result, pending = _partition_cached(container_ids, _stats_cache)
    if not pending:
        return result
    res = _safe_run(
        runner, ["docker", "stats", "--no-stream", "--format", "json", *pending], timeout=timeout
    )
    by_id = _parse_stats_payload(res.stdout) if res is not None and res.returncode == 0 else {}
    now = time.monotonic()
    for cid in pending:
        entry = by_id.get(cid) or by_id.get(cid[:12])
        result[cid] = entry
        _stats_cache[cid] = (now, entry) if entry is not None else (now, {})
    return result


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
    cluster state so we can distinguish `stopped` from `missing` without a
    second status call. Container inspect/stats are cached by container_id.
    """
    sampled_at = datetime.now(UTC)

    requires_docker = bool(getattr(runner, "requires_docker", True))
    if requires_docker and not docker_available():
        return ClusterResourceSnapshot(
            container=None,
            metrics=None,
            unavailability_reason="docker_unavailable",
            sampled_at=sampled_at,
        )

    if state is PostgresClusterState.STOPPED:
        return ClusterResourceSnapshot(
            container=None,
            metrics=None,
            unavailability_reason="stopped",
            sampled_at=sampled_at,
        )

    container_id = resolve_container_id(
        compose_file, compose_project_name, service, runner=runner, timeout=timeout
    )
    if container_id is None:
        return ClusterResourceSnapshot(
            container=None,
            metrics=None,
            unavailability_reason="missing",
            sampled_at=sampled_at,
        )

    inspect_map = inspect_containers((container_id,), runner=runner, timeout=timeout)
    inspect_entry = inspect_map.get(container_id)
    if not inspect_entry:
        return ClusterResourceSnapshot(
            container=None,
            metrics=None,
            unavailability_reason="inspect_failed",
            sampled_at=sampled_at,
        )

    container = _build_container(inspect_entry)

    stats_map = stats_containers((container_id,), runner=runner, timeout=timeout)
    stats_entry = stats_map.get(container_id)
    if not stats_entry:
        return ClusterResourceSnapshot(
            container=container,
            metrics=None,
            unavailability_reason="stats_failed",
            sampled_at=sampled_at,
        )

    metrics = _build_metrics(stats_entry, sampled_at)
    return ClusterResourceSnapshot(
        container=container,
        metrics=metrics,
        unavailability_reason=None,
        sampled_at=sampled_at,
    )


def _build_container(inspect_entry: dict[str, object]) -> ClusterContainer:
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
    short = _short_id(ident) if isinstance(ident, str) else None
    pid_scope = _pid_scope() if pid is not None else PidScope.UNAVAILABLE
    return ClusterContainer(
        id=short,
        name=name if isinstance(name, str) else None,
        image=image,
        pid=pid,
        pid_scope=pid_scope,
    )


def _build_metrics(stats_entry: dict[str, object], sampled_at: datetime) -> ClusterMetrics:
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
    "cluster_resource_snapshot",
    "inspect_containers",
    "resolve_container_id",
    "stats_containers",
]
