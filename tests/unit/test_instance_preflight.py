from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import InstanceConfig, OdooClientConfig
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


@pytest.mark.unit
def test_manual_instance_no_preflight() -> None:
    instance = _make_instance(cluster=None)
    with patch("odoo_instance_sdk.resources.instance.run_foreground_process", return_value=0):
        exit_code = instance.run_foreground()
    assert exit_code == 0


@pytest.mark.unit
def test_preflight_runs_before_run_foreground() -> None:
    cluster = _FakeCluster()
    instance = _make_instance(cluster=cluster)
    with patch("odoo_instance_sdk.resources.instance.run_foreground_process", return_value=0):
        instance.run_foreground()
    assert cluster.ensure_calls == 1


@pytest.mark.unit
def test_preflight_runs_before_shell() -> None:
    cluster = _FakeCluster()
    instance = _make_instance(cluster=cluster)
    with patch("odoo_instance_sdk.resources.instance.run_foreground_process", return_value=0):
        instance.shell(args=())
    assert cluster.ensure_calls == 1


@pytest.mark.unit
def test_preflight_runs_before_run_shell_script() -> None:
    cluster = _FakeCluster()
    instance = _make_instance(cluster=cluster)
    from odoo_instance_sdk.models import CommandResult

    fake_result = CommandResult(args=[], returncode=0, stdout="", stderr="", duration=0.0)
    with patch(
        "odoo_instance_sdk.internal.server._run_captured_shell",
        return_value=fake_result,
    ):
        instance.run_shell_script("print(1)")
    assert cluster.ensure_calls == 1


@pytest.mark.unit
def test_preflight_runs_once_per_call() -> None:
    cluster = _FakeCluster()
    instance = _make_instance(cluster=cluster)
    with patch("odoo_instance_sdk.resources.instance.run_foreground_process", return_value=0):
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
