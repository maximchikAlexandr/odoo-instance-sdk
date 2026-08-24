from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessTreeResult:
    child_pids: tuple[int, ...]
    process_count: int
    cpu_percent: float | None
    rss_bytes: int | None


@dataclass(frozen=True, slots=True)
class CpuPoint:
    """In-memory CPU sample for two-sample delta. Caller stores by (pid, create_time)."""

    times_cpu: float
    timestamp: float


def _aggregate_cpu_times(proc: Any, children: list[Any]) -> float:
    """Sum user+system cpu times for root + accessible children.

    ponytail: aggregated tree CPU, dead children drop to 0 between samples
    (they're absent from the new children list, so the delta only reflects
    survivors + root). Matches "sum across the tree" per design D5.
    """
    total = 0.0
    with contextlib.suppress(Exception):
        ct = proc.cpu_times()
        total += ct.user + ct.system
    for child in children:
        with contextlib.suppress(Exception):
            cct = child.cpu_times()
            total += cct.user + cct.system
    return total


def _root_rss(proc: Any) -> int | None:
    with contextlib.suppress(Exception):
        return int(proc.memory_info().rss)
    return None


def _collect_children(proc: Any, psutil: Any) -> list[Any]:
    """Recursive children; root-level AccessDenied/NoSuchProcess/Zombie → empty."""
    try:
        return list(proc.children(recursive=True))
    except psutil.AccessDenied:
        return []
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return []


def _child_metrics(child: Any, psutil: Any) -> tuple[int, int] | None:
    """Return (pid, rss) for one child, or None if the child must be skipped.

    Skip on NoSuchProcess/ZombieProcess/AccessDenied (per spec D5: child
    AccessDenied → skip that child).
    """
    try:
        pid = child.pid
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return None
    rss = 0
    with contextlib.suppress(psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        rss = int(child.memory_info().rss)
    return pid, rss


def _build_tree(proc: Any, psutil: Any) -> tuple[list[int], int, list[Any], int | None]:
    """Returns (child_pids, rss_bytes_sum_for_children, children_list, root_rss)."""
    children = _collect_children(proc, psutil)
    child_pids: list[int] = []
    children_rss = 0
    for child in children:
        cm = _child_metrics(child, psutil)
        if cm is None:
            continue
        child_pids.append(cm[0])
        children_rss += cm[1]
    root_rss = _root_rss(proc)
    return child_pids, children_rss, children, root_rss


def collect_process_tree(
    root_pid: int,
    create_time: float,
    *,
    prev_cpu_point: CpuPoint | None,
) -> tuple[ProcessTreeResult, CpuPoint] | None:
    """Collect Odoo process-tree metrics via psutil.

    Returns ``None`` when PID is missing, ``create_time`` mismatches (PID reuse),
    or the root raises ``NoSuchProcess``/``AccessDenied``/``ZombieProcess``.
    On success returns ``(ProcessTreeResult, CpuPoint)`` so the caller can
    store the CPU point keyed by ``(pid, create_time)``.

    ponytail: deviates from D5 signature ``-> ProcessTreeResult | None`` by
    returning the new ``CpuPoint`` alongside the result. The collector (Block H)
    must persist the prev point to compute deltas; returning it here is the
    minimal way to let the caller store it without a mutable callback or
    shared mutable state.
    """
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        from odoo_instance_sdk.exceptions import MonitorExtrasMissingError

        raise MonitorExtrasMissingError(
            "psutil is not installed; pip install odoo-instance-sdk[metrics]"
        ) from None

    if not psutil.pid_exists(root_pid):
        return None

    try:
        proc = psutil.Process(root_pid)
        if proc.create_time() != create_time:
            return None
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        return None
    except psutil.ZombieProcess:
        return None

    child_pids, children_rss, children, root_rss = _build_tree(proc, psutil)

    now = time.monotonic()
    total_times_cpu = _aggregate_cpu_times(proc, children)
    new_point = CpuPoint(times_cpu=total_times_cpu, timestamp=now)

    cpu_percent: float | None
    if prev_cpu_point is None:
        cpu_percent = None
    else:
        elapsed = now - prev_cpu_point.timestamp
        if elapsed > 0:
            delta_cpu = total_times_cpu - prev_cpu_point.times_cpu
            cpu_percent = max(delta_cpu / elapsed * 100.0, 0.0)
        else:
            cpu_percent = None

    rss_bytes = (root_rss + children_rss) if root_rss is not None else None
    return (
        ProcessTreeResult(
            child_pids=tuple(child_pids),
            process_count=1 + len(child_pids),
            cpu_percent=cpu_percent,
            rss_bytes=rss_bytes,
        ),
        new_point,
    )
