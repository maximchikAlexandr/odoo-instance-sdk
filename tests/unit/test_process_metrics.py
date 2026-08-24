from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from odoo_instance_sdk.exceptions import MonitorExtrasMissingError
from odoo_instance_sdk.internal.process_metrics import (
    CpuPoint,
    collect_process_tree,
)


def _make_psutil(
    *,
    pid_exists: bool = True,
    root: Any = None,
) -> Any:
    """Build a fake `psutil` module with the exception classes used by the code."""
    mod: Any = types.ModuleType("psutil")

    class ProcessError(Exception):
        pass

    class NoSuchProcess(ProcessError):
        pass

    class AccessDenied(ProcessError):
        pass

    class ZombieProcess(ProcessError):
        pass

    mod.NoSuchProcess = NoSuchProcess
    mod.AccessDenied = AccessDenied
    mod.ZombieProcess = ZombieProcess

    def _pid_exists(pid: int) -> bool:
        return pid_exists

    mod.pid_exists = _pid_exists

    def _process(pid: int) -> Any:
        if root is None or pid != root["pid"]:
            raise NoSuchProcess(pid)
        return root["instance"]

    mod.Process = _process
    return mod


class FakeProcess:
    def __init__(
        self,
        pid: int,
        create_time: float,
        children: list[FakeProcess] | None = None,
        cpu_times: tuple[float, float] = (0.0, 0.0),
        rss: int = 0,
        access_denied: bool = False,
        zombie: bool = False,
        no_such_process: bool = False,
    ) -> None:
        self.pid = pid
        self._create_time = create_time
        self._children = children or []
        self._cpu_times = cpu_times
        self._rss = rss
        self.access_denied = access_denied
        self.zombie = zombie
        self.no_such_process = no_such_process

    def create_time(self) -> float:
        return self._create_time

    def children(self, recursive: bool = True) -> list[FakeProcess]:
        return self._children

    def cpu_times(self) -> Any:
        if self.access_denied:
            raise _current_psutil.AccessDenied(self.pid)
        if self.zombie:
            raise _current_psutil.ZombieProcess(self.pid)
        if self.no_such_process:
            raise _current_psutil.NoSuchProcess(self.pid)
        return types.SimpleNamespace(user=self._cpu_times[0], system=self._cpu_times[1])

    def memory_info(self) -> Any:
        if self.access_denied:
            raise _current_psutil.AccessDenied(self.pid)
        if self.zombie:
            raise _current_psutil.ZombieProcess(self.pid)
        if self.no_such_process:
            raise _current_psutil.NoSuchProcess(self.pid)
        return types.SimpleNamespace(rss=self._rss)


_current_psutil: Any = _make_psutil(pid_exists=True, root=None)


def _install_psutil(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    global _current_psutil  # noqa: PLW0603
    _current_psutil = fake
    monkeypatch.setitem(sys.modules, "psutil", fake)


def _uninstall_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "psutil", raising=False)


@pytest.mark.unit
def test_pid_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _make_psutil(pid_exists=False, root=None)
    _install_psutil(monkeypatch, fake)
    result = collect_process_tree(1, 0.0, prev_cpu_point=None)
    assert result is None


@pytest.mark.unit
def test_create_time_mismatch_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProcess(pid=1, create_time=100.0, rss=42)
    fake = _make_psutil(root={"pid": 1, "instance": proc})
    _install_psutil(monkeypatch, fake)
    result = collect_process_tree(1, 200.0, prev_cpu_point=None)
    assert result is None


@pytest.mark.unit
def test_root_no_such_process(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _make_psutil(pid_exists=True, root=None)
    fake.Process = lambda pid: (_ for _ in ()).throw(fake.NoSuchProcess(pid))
    _install_psutil(monkeypatch, fake)
    result = collect_process_tree(1, 0.0, prev_cpu_point=None)
    assert result is None


@pytest.mark.unit
def test_root_access_denied_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _make_psutil(pid_exists=True, root=None)
    fake.Process = lambda pid: (_ for _ in ()).throw(fake.AccessDenied(pid))
    _install_psutil(monkeypatch, fake)
    result = collect_process_tree(1, 0.0, prev_cpu_point=None)
    assert result is None


@pytest.mark.unit
def test_root_zombie_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _make_psutil(pid_exists=True, root=None)
    fake.Process = lambda pid: (_ for _ in ()).throw(fake.ZombieProcess(pid))
    _install_psutil(monkeypatch, fake)
    result = collect_process_tree(1, 0.0, prev_cpu_point=None)
    assert result is None


@pytest.mark.unit
def test_live_root_no_children(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProcess(pid=1, create_time=100.0, rss=42, cpu_times=(1.0, 2.0))
    fake = _make_psutil(root={"pid": 1, "instance": proc})
    _install_psutil(monkeypatch, fake)
    outcome = collect_process_tree(1, 100.0, prev_cpu_point=None)
    assert outcome is not None
    result, point = outcome
    assert result.child_pids == ()
    assert result.process_count == 1
    assert result.cpu_percent is None
    assert result.rss_bytes == 42
    assert isinstance(point, CpuPoint)
    assert point.times_cpu == 3.0


@pytest.mark.unit
def test_two_children(monkeypatch: pytest.MonkeyPatch) -> None:
    child_a = FakeProcess(pid=2, create_time=100.0, rss=10, cpu_times=(0.5, 0.5))
    child_b = FakeProcess(pid=3, create_time=100.0, rss=20, cpu_times=(1.0, 1.0))
    proc = FakeProcess(
        pid=1,
        create_time=100.0,
        rss=42,
        children=[child_a, child_b],
        cpu_times=(1.0, 2.0),
    )
    fake = _make_psutil(root={"pid": 1, "instance": proc})
    _install_psutil(monkeypatch, fake)
    outcome = collect_process_tree(1, 100.0, prev_cpu_point=None)
    assert outcome is not None
    result, _ = outcome
    assert result.child_pids == (2, 3)
    assert result.process_count == 3
    assert result.rss_bytes == 72
    assert result.cpu_percent is None


@pytest.mark.unit
def test_cpu_two_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    child_a = FakeProcess(pid=2, create_time=100.0, rss=10, cpu_times=(0.5, 0.5))
    proc = FakeProcess(
        pid=1,
        create_time=100.0,
        rss=42,
        children=[child_a],
        cpu_times=(1.0, 2.0),
    )
    fake = _make_psutil(root={"pid": 1, "instance": proc})
    _install_psutil(monkeypatch, fake)

    first_outcome = collect_process_tree(1, 100.0, prev_cpu_point=None)
    assert first_outcome is not None
    first, point1 = first_outcome
    assert first.cpu_percent is None
    assert point1.times_cpu == 4.0

    proc._cpu_times = (3.0, 5.0)  # root delta = 5.0s cpu
    child_a._cpu_times = (1.5, 1.5)  # child delta = 2.0s cpu
    total_delta_cpu = 7.0

    prev = CpuPoint(times_cpu=point1.times_cpu, timestamp=point1.timestamp - 10.0)
    second_outcome = collect_process_tree(1, 100.0, prev_cpu_point=prev)
    assert second_outcome is not None
    second, point2 = second_outcome
    assert second.cpu_percent is not None
    elapsed = point2.timestamp - prev.timestamp
    assert second.cpu_percent == pytest.approx(total_delta_cpu / elapsed * 100.0)
    assert point2.times_cpu == 4.0 + 7.0


@pytest.mark.unit
def test_child_access_denied_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    child_a = FakeProcess(pid=2, create_time=100.0, rss=10, cpu_times=(0.5, 0.5))
    child_b = FakeProcess(pid=3, create_time=100.0, access_denied=True, rss=20)
    proc = FakeProcess(
        pid=1,
        create_time=100.0,
        rss=42,
        children=[child_a, child_b],
        cpu_times=(1.0, 2.0),
    )
    fake = _make_psutil(root={"pid": 1, "instance": proc})
    _install_psutil(monkeypatch, fake)
    outcome = collect_process_tree(1, 100.0, prev_cpu_point=None)
    assert outcome is not None
    result, _ = outcome
    assert result.child_pids == (2, 3)
    assert result.process_count == 3
    assert result.rss_bytes == 52


@pytest.mark.unit
def test_monitor_extras_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _uninstall_psutil(monkeypatch)
    monkeypatch.setitem(sys.modules, "psutil", None)
    with pytest.raises(MonitorExtrasMissingError) as excinfo:
        collect_process_tree(1, 0.0, prev_cpu_point=None)
    assert "pip install odoo-instance-sdk[metrics]" in str(excinfo.value)
