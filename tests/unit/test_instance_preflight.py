from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
from odoo_instance_sdk.internal.proc import ProcessHandle, RecordingExecutor
from odoo_instance_sdk.models import PostgresClusterState, StartConfig
from odoo_instance_sdk.resources.instance import OdooInstance


def _make_instance(cluster: Any = None) -> OdooInstance:
    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    config = InstanceConfig(
        base_url="http://127.0.0.1:8069",
        start_config=StartConfig(http_port=8069, config_path="/tmp/odoo.conf"),
    )
    return OdooInstance(
        config=config,
        _client=client,
        _postgres_cluster=cluster,
    )


def _executor(step_id: str) -> RecordingExecutor:
    process = MagicMock()
    process.pid = 4242
    process.poll.return_value = 0
    process.wait.return_value = 0
    return RecordingExecutor(handles={step_id: ProcessHandle(process, (), 4242, 4242, True)})


class _FakeCluster:
    def __init__(self, *, raise_on_ensure: Exception | None = None) -> None:
        self.ensure_calls = 0
        self._raise = raise_on_ensure

    def ensure_running(self, timeout: float = 60.0) -> None:
        self.ensure_calls += 1
        if self._raise is not None:
            raise self._raise

    def status(self) -> PostgresClusterState:
        return PostgresClusterState.HEALTHY

    @property
    def mode(self) -> str:
        return "compose"

    @property
    def owned(self) -> bool:
        return True

    @property
    def endpoint(self) -> str:
        return "127.0.0.1:5468"

    def to_diagnostic_dict(self) -> dict[str, object]:
        return {"mode": "compose", "owned": True, "endpoint": "127.0.0.1:5468"}


class _EventCluster(_FakeCluster):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    def ensure_running(self, timeout: float = 60.0) -> None:
        self._events.append("ensure")
        super().ensure_running(timeout)


@pytest.mark.unit
def test_manual_instance_no_preflight() -> None:
    instance = _make_instance(cluster=None)
    with patch(
        "odoo_instance_sdk.resources.instance.SubprocessExecutor",
        return_value=_executor("instance.foreground"),
    ):
        exit_code = instance.run_foreground()
    assert exit_code == 0


@pytest.mark.unit
def test_preflight_runs_before_run_foreground() -> None:
    cluster = _FakeCluster()
    instance = _make_instance(cluster=cluster)
    with patch(
        "odoo_instance_sdk.resources.instance.SubprocessExecutor",
        return_value=_executor("instance.foreground"),
    ):
        instance.run_foreground()
    assert cluster.ensure_calls == 1


@pytest.mark.unit
def test_preflight_runs_before_shell() -> None:
    cluster = _FakeCluster()
    instance = _make_instance(cluster=cluster)
    with patch(
        "odoo_instance_sdk.resources.instance.SubprocessExecutor",
        return_value=_executor("instance.shell"),
    ):
        instance.shell(args=())
    assert cluster.ensure_calls == 1


@pytest.mark.unit
def test_preflight_runs_before_run_shell_script() -> None:
    cluster = _FakeCluster()
    instance = _make_instance(cluster=cluster)
    with patch(
        "odoo_instance_sdk.resources.instance.SubprocessExecutor",
        return_value=_executor("instance.shell_script"),
    ):
        instance.run_shell_script("print(1)")
    assert cluster.ensure_calls == 1


@pytest.mark.unit
def test_preflight_runs_once_per_call() -> None:
    cluster = _FakeCluster()
    instance = _make_instance(cluster=cluster)
    with patch(
        "odoo_instance_sdk.resources.instance.SubprocessExecutor",
        return_value=_executor("instance.foreground"),
    ):
        instance.run_foreground()
        instance.run_foreground()
    assert cluster.ensure_calls == 2  # one per call


@pytest.mark.unit
def test_preflight_propagates_cluster_error() -> None:
    from odoo_instance_sdk.exceptions import PostgresClusterUnreachableError

    cluster = _FakeCluster(raise_on_ensure=PostgresClusterUnreachableError("nope"))
    instance = _make_instance(cluster=cluster)
    with pytest.raises(PostgresClusterUnreachableError):
        instance.run_foreground()
    assert cluster.ensure_calls == 1


@pytest.mark.unit
def test_preflight_event_precedes_foreground_shell_and_script_spawn() -> None:
    events: list[str] = []
    instance = _make_instance(cluster=_EventCluster(events))
    from odoo_instance_sdk.internal.proc import PreparedProcess

    class EventExecutor(RecordingExecutor):
        def spawn(self, step: PreparedProcess) -> ProcessHandle:
            events.append("spawn")
            return super().spawn(step)

        def execute(self, step: PreparedProcess) -> object:
            events.append("shell-spawn")
            return super().execute(step)

    handle = _executor("instance.foreground").handles["instance.foreground"]
    executor = EventExecutor(handles={"instance.foreground": handle, "instance.shell": handle})

    with patch("odoo_instance_sdk.resources.instance.SubprocessExecutor", return_value=executor):
        instance.run_foreground()
        assert events[:2] == ["ensure", "spawn"]
        events.clear()
        instance.shell(args=())
        assert events[:2] == ["ensure", "spawn"]
        events.clear()
        instance.run_shell_script("print(1)")
        assert events[:2] == ["ensure", "shell-spawn"]


@pytest.mark.unit
def test_preflight_event_precedes_exclusive_script_operation() -> None:
    events: list[str] = []
    instance = _make_instance(cluster=_EventCluster(events))

    class EventExecutor(RecordingExecutor):
        def execute(self, step: object) -> object:
            events.append("exclusive-spawn")
            return super().execute(step)  # type: ignore[arg-type]

    with patch(
        "odoo_instance_sdk.resources.instance.SubprocessExecutor",
        return_value=EventExecutor(),
    ):
        instance._run_shell_script_exclusive("print(1)")
    assert events[:2] == ["ensure", "exclusive-spawn"]


@pytest.mark.unit
def test_exclusive_script_rechecks_cluster_after_claiming_artifact_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    instance = _make_instance(cluster=_EventCluster(events))

    @contextlib.contextmanager
    def claimed_operation(_self: OdooInstance, *, exclusive: bool) -> Iterator[None]:
        assert exclusive is True
        events.append("lock-claimed")
        yield
        events.append("lock-released")

    monkeypatch.setattr(OdooInstance, "_artifact_operation", claimed_operation)
    with patch(
        "odoo_instance_sdk.resources.instance.SubprocessExecutor",
        return_value=RecordingExecutor(),
    ):
        instance._run_shell_script_exclusive("print(1)", commit=True)

    assert events == ["lock-claimed", "ensure", "lock-released"]
