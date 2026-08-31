from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import pytest
from click.testing import CliRunner

from odoo_instance_sdk.cli import cli
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.cli_format import human_bytes
from odoo_instance_sdk.models import (
    ClusterContainer,
    ClusterMetrics,
    ClusterResourceSnapshot,
    ClusterUnavailabilityReason,
    PidScope,
    PostgresClusterState,
)
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster

T = TypeVar("T")


def _command(callback: Callable[[], T]) -> Command[T]:
    return Command.create(ExecutionPlan(), lambda _context: callback(), ())


def _write_project(tmp_path: Path, *, mode: str = "compose") -> Path:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    postgres = (
        PostgresProjectConfig(mode="compose", image="pg", port=5468, user="odoo")
        if mode == "compose"
        else None
    )
    cfg = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        source_config=tmp_path / "odoo.conf",
        postgres=postgres,
    )
    (tmp_path / "odoo.conf").write_text("[options]\ndb_host = 127.0.0.1\ndb_port = 5468\n")
    (manifest_dir / "project.toml").write_text(cfg.to_manifest())
    return tmp_path


def _resource_snapshot(
    *,
    unavailability_reason: ClusterUnavailabilityReason | None = None,
    with_metrics: bool = True,
) -> ClusterResourceSnapshot:
    now = datetime.now(UTC)
    metrics = (
        ClusterMetrics(
            cpu_percent=4.2,
            memory_usage_bytes=512 * 1024 * 1024,
            memory_limit_bytes=None,
            volume_usage_bytes=12 * 1024**3,
            sampled_at=now,
        )
        if with_metrics
        else None
    )
    container = (
        ClusterContainer(
            id="4fc83d" + "a" * 58,
            name="odoo_pg_comerta",
            image="postgres:16",
            pid=9124,
            pid_scope=PidScope.DOCKER_VM,
        )
        if unavailability_reason is None
        else None
    )
    return ClusterResourceSnapshot(
        container=container,
        metrics=metrics,
        unavailability_reason=unavailability_reason,
        sampled_at=now if unavailability_reason is None else None,
    )


class _ComposeCluster:
    mode = "compose"
    owned = True
    endpoint_host = "127.0.0.1"
    endpoint_port = 5468

    def __init__(self, *, state: PostgresClusterState, resource: ClusterResourceSnapshot) -> None:
        self._state = state
        self._resource = resource
        self.resource_calls = 0

    def status(self) -> PostgresClusterState:
        return self._state

    def status_command(self) -> Command[PostgresClusterState]:
        return _command(self.status)

    def resource_snapshot(self) -> ClusterResourceSnapshot:
        self.resource_calls += 1
        return self._resource


class _ExternalCluster:
    mode = "external"
    owned = False
    endpoint_host = "127.0.0.1"
    endpoint_port = 5432
    resource_calls = 0

    def __init__(self, state: PostgresClusterState = PostgresClusterState.HEALTHY) -> None:
        self._state = state

    def status(self) -> PostgresClusterState:
        return self._state

    def status_command(self) -> Command[PostgresClusterState]:
        return _command(self.status)

    def resource_snapshot(self) -> ClusterResourceSnapshot:
        self.resource_calls += 1
        raise AssertionError("resource_snapshot must not be called for external clusters")


@pytest.mark.unit
def test_postgres_status_json_includes_container_and_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(tmp_path)
    cluster = _ComposeCluster(
        state=PostgresClusterState.HEALTHY,
        resource=_resource_snapshot(),
    )
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    result = CliRunner().invoke(cli, ["--project", str(root), "postgres", "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "postgres.status"
    snap = payload["result"]
    assert snap["state"] == "healthy"
    assert snap["mode"] == "compose"
    assert snap["owned"] is True
    assert snap["container"]["id"].startswith("4fc83d")
    assert snap["container"]["pid"] == 9124
    assert snap["container"]["pid_scope"] == "docker_vm"
    assert snap["metrics"]["cpu_percent"] == 4.2
    assert snap["metrics"]["memory_usage_bytes"] == 512 * 1024 * 1024
    assert cluster.resource_calls == 1


@pytest.mark.unit
def test_postgres_status_human_includes_container_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(tmp_path)
    cluster = _ComposeCluster(
        state=PostgresClusterState.HEALTHY,
        resource=_resource_snapshot(),
    )
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    result = CliRunner().invoke(cli, ["--project", str(root), "postgres", "status"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "container=4fc83d" in out
    assert "pid=vm:9124" in out
    assert "cpu=4.2%" in out
    assert "ram=512.0 MiB" in out


@pytest.mark.unit
def test_human_bytes_preserves_fractional_binary_units() -> None:
    assert human_bytes(1536) == "1.5 KiB"
    assert human_bytes(5 * 1024**2 + 512 * 1024) == "5.5 MiB"


@pytest.mark.unit
def test_postgres_status_external_skips_resource_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(tmp_path, mode="external")
    cluster = _ExternalCluster()
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    from odoo_instance_sdk.internal.address import AddressState

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.probe_address",
        lambda host, port: AddressState.OCCUPIED,
    )
    result = CliRunner().invoke(cli, ["--project", str(root), "postgres", "status", "--json"])
    assert result.exit_code == 0, result.output
    snap = json.loads(result.output)["result"]
    assert snap["mode"] == "external"
    assert snap["container"] is None
    assert snap["metrics"] is None
    assert snap["unavailability_reason"] == "external_not_owned"
    assert cluster.resource_calls == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "exit_code"),
    [
        (PostgresClusterState.HEALTHY, 0),
        (PostgresClusterState.UNREACHABLE, 1),
        (PostgresClusterState.UNHEALTHY, 1),
    ],
)
def test_postgres_status_external_exit_tracks_tcp_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: PostgresClusterState, exit_code: int
) -> None:
    root = _write_project(tmp_path, mode="external")
    cluster = _ExternalCluster(state)
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    result = CliRunner().invoke(cli, ["--project", str(root), "postgres", "status", "--json"])
    assert result.exit_code == exit_code, result.output
    assert json.loads(result.output)["result"]["unavailability_reason"] == "external_not_owned"


@pytest.mark.unit
def test_postgres_status_stopped_compose_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(tmp_path)
    cluster = _ComposeCluster(
        state=PostgresClusterState.STOPPED,
        resource=_resource_snapshot(unavailability_reason="stopped", with_metrics=False),
    )
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    result = CliRunner().invoke(cli, ["--project", str(root), "postgres", "status", "--json"])
    assert result.exit_code == 0, result.output
    snap = json.loads(result.output)["result"]
    assert snap["state"] == "stopped"
    assert snap["unavailability_reason"] == "stopped"
    assert snap["container"] is None


@pytest.mark.unit
def test_postgres_status_docker_unavailable_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_project(tmp_path)
    cluster = _ComposeCluster(
        state=PostgresClusterState.HEALTHY,
        resource=_resource_snapshot(unavailability_reason="docker_unavailable", with_metrics=False),
    )
    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(lambda _path: cluster))
    result = CliRunner().invoke(cli, ["--project", str(root), "postgres", "status", "--json"])
    assert result.exit_code == 0, result.output
    snap = json.loads(result.output)["result"]
    assert snap["unavailability_reason"] == "docker_unavailable"
