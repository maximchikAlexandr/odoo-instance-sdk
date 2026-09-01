from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast


class _CpuTimes(Protocol):
    user: float
    system: float


class _MemoryInfo(Protocol):
    rss: int


class _Process(Protocol):
    pid: int

    def memory_info(self) -> _MemoryInfo: ...
    def cpu_times(self) -> _CpuTimes: ...
    def children(self, recursive: bool = False) -> Sequence[_Process]: ...


class _Psutil(Protocol):
    NoSuchProcess: type[BaseException]
    ZombieProcess: type[BaseException]
    AccessDenied: type[BaseException]


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


def _child_metrics(child: _Process, psutil: _Psutil) -> tuple[int, int, float] | None:
    """Return one complete child sample, or ``None`` when it must be skipped.

    Skip on NoSuchProcess/ZombieProcess/AccessDenied (per spec D5: child
    AccessDenied → skip that child).
    """
    try:
        pid = child.pid
        rss = int(child.memory_info().rss)
        cpu_times = child.cpu_times()
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return None
    return pid, rss, cpu_times.user + cpu_times.system


def _build_tree(proc: _Process, psutil: _Psutil) -> tuple[list[int], int, float]:
    """Return child PID, RSS, and CPU totals.

    Root process operations intentionally propagate psutil lifecycle errors to
    ``collect_process_tree``. A child that cannot provide its full sample is
    omitted, rather than being represented as a zero-RSS process.
    """
    children = list(proc.children(recursive=True))
    child_pids: list[int] = []
    children_rss = 0
    children_cpu = 0.0
    for child in children:
        cm = _child_metrics(child, psutil)
        if cm is None:
            continue
        child_pids.append(cm[0])
        children_rss += cm[1]
        children_cpu += cm[2]
    return child_pids, children_rss, children_cpu


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
    import psutil

    try:
        if not psutil.pid_exists(root_pid):
            return None
        proc = psutil.Process(root_pid)
        if proc.create_time() != create_time:
            return None
        child_pids, children_rss, children_cpu = _build_tree(
            cast("_Process", proc), cast("_Psutil", psutil)
        )
        root_rss = int(proc.memory_info().rss)
        root_cpu_times = proc.cpu_times()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    now = time.monotonic()
    total_times_cpu = root_cpu_times.user + root_cpu_times.system + children_cpu
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

    rss_bytes = root_rss + children_rss
    return (
        ProcessTreeResult(
            child_pids=tuple(child_pids),
            process_count=1 + len(child_pids),
            cpu_percent=cpu_percent,
            rss_bytes=rss_bytes,
        ),
        new_point,
    )
