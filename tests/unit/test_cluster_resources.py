from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from odoo_instance_sdk.internal import cluster_resources
from odoo_instance_sdk.internal.cluster_resources import (
    _parse_cpu_percent,
    _parse_mem_value,
    inspect_containers,
    stats_containers,
)
from odoo_instance_sdk.models import (
    ClusterResourceSnapshot,
    PidScope,
    PostgresClusterState,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster


def _cp(
    args: Sequence[str], rc: int, stdout: str, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(args), rc, stdout, stderr)


class FakeDockerRunner:
    """Duck-typed ComposeRunner returning scripted docker compose/inspect/stats output."""

    def __init__(
        self,
        *,
        ps_rows: list[dict[str, object]] | None = None,
        ps_rc: int = 0,
        inspect_payload: object | None = None,
        inspect_rc: int = 0,
        stats_lines: list[dict[str, object]] | None = None,
        stats_rc: int = 0,
        volume_payload: object | None = None,
        requires_docker: bool = False,
    ) -> None:
        # Scripted runners never need host Docker unless a test explicitly
        # exercises the availability guard.
        self.requires_docker = requires_docker
        self.calls: list[list[str]] = []
        self._ps_rows = ps_rows if ps_rows is not None else []
        self._ps_rc = ps_rc
        self._inspect_payload = inspect_payload
        self._inspect_rc = inspect_rc
        self._stats_lines = stats_lines if stats_lines is not None else []
        self._stats_rc = stats_rc
        self._volume_payload = volume_payload if volume_payload is not None else {"Volumes": []}

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        joined = " ".join(args)
        if " compose " in joined and " ps " in joined:
            out = "\n".join(json.dumps(r) for r in self._ps_rows)
            return _cp(args, self._ps_rc, out, "" if self._ps_rc == 0 else "ps fail")
        if args[:2] == ["docker", "inspect"]:
            out = json.dumps(self._inspect_payload) if self._inspect_payload is not None else ""
            return _cp(args, self._inspect_rc, out, "" if self._inspect_rc == 0 else "inspect fail")
        if args[:2] == ["docker", "stats"]:
            out = "\n".join(json.dumps(r) for r in self._stats_lines)
            return _cp(args, self._stats_rc, out, "" if self._stats_rc == 0 else "stats fail")
        if args[:3] == ["docker", "system", "df"]:
            return _cp(args, 0, json.dumps(self._volume_payload))
        return _cp(args, 0, "", "")


FULL_ID = "4fc83d" + "a1b2c3" + "d4e5f6" + "0123" + "4567" + "89ab"  # 32 hex chars
SHORT_ID = FULL_ID[:12]


def _healthy_inspect() -> dict[str, object]:
    return {
        "Id": FULL_ID,
        "Name": "/odcli_pg_x_postgres",
        "Config": {
            "Image": "postgres:16",
            "Env": [
                "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password",
                "PGDATA=/var/lib/postgresql/data",
            ],
        },
        "State": {"Pid": 9124, "Running": True},
    }


@pytest.mark.unit
def test_resource_snapshot_reads_named_volume_usage() -> None:
    inspect = _healthy_inspect()
    inspect["Mounts"] = [{"Type": "volume", "Name": "odcli_pg_x_data"}]
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[inspect],
        stats_lines=[_healthy_stats()],
        volume_payload={"Volumes": [{"Name": "odcli_pg_x_data", "Size": "1.5GiB"}]},
    )
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.metrics is not None
    assert snap.metrics.volume_usage_bytes == int(1.5 * 1024**3)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("volume_payload", "expected"),
    [
        ({"Volumes": [{"Name": "another", "Size": "1GiB"}]}, None),
        ({"Volumes": [{"Name": "odcli_pg_x_data", "Size": "broken"}]}, None),
        ({"Volumes": "not-an-array"}, None),
    ],
)
def test_resource_snapshot_named_volume_payload_degrades_per_volume(
    volume_payload: object, expected: int | None
) -> None:
    inspect = _healthy_inspect()
    inspect["Mounts"] = [{"Type": "volume", "Name": "odcli_pg_x_data"}]
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[inspect],
        stats_lines=[_healthy_stats()],
        volume_payload=volume_payload,
    )
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.metrics is not None
    assert snap.metrics.volume_usage_bytes == expected


def _healthy_stats() -> dict[str, object]:
    return {
        "container": FULL_ID,
        "CPUPerc": "4.20%",
        "MemUsage": "512MiB / 1GiB",
        "MemPerc": "50.00%",
    }


@pytest.mark.unit
def test_parse_mem_value_units() -> None:
    assert _parse_mem_value("512MiB") == 512 * 1024**2
    assert _parse_mem_value("1GiB") == 1024**3
    assert _parse_mem_value("100B") == 100
    assert _parse_mem_value("1.5GB") == int(1.5 * 1000**3)
    assert _parse_mem_value("not a number") is None
    assert _parse_mem_value("12KiB") == 12 * 1024


@pytest.mark.unit
def test_parse_cpu_percent() -> None:
    assert _parse_cpu_percent("4.20%") == 4.2
    assert _parse_cpu_percent("4.2") == 4.2
    assert _parse_cpu_percent("") is None
    assert _parse_cpu_percent("xx%") is None


@pytest.mark.unit
def test_resource_snapshot_external_returns_none(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cluster = PostgresCluster(
        _repository_root=root,
        _project_id="x",
        _mode="external",
        _endpoint_host="127.0.0.1",
        _endpoint_port=5432,
        _compose_runner=FakeDockerRunner(),
    )
    assert cluster.resource_snapshot() is None


@pytest.mark.unit
def test_resource_snapshot_stopped() -> None:
    runner = FakeDockerRunner(ps_rows=[], requires_docker=True)
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.STOPPED,
    )
    assert snap.unavailability_reason == "stopped"
    assert snap.container is None
    assert snap.metrics is None
    assert snap.sampled_at is None


@pytest.mark.unit
def test_resource_snapshot_docker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cluster_resources, "docker_available", lambda: False)
    runner = FakeDockerRunner(requires_docker=True)
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.unavailability_reason == "docker_unavailable"
    assert snap.container is None
    assert snap.metrics is None


@pytest.mark.unit
def test_resource_snapshot_missing() -> None:
    runner = FakeDockerRunner(ps_rows=[])
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.unavailability_reason == "missing"
    assert snap.container is None
    assert snap.metrics is None


@pytest.mark.unit
def test_resource_snapshot_healthy_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[_healthy_inspect()],
        stats_lines=[_healthy_stats()],
    )
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.unavailability_reason is None
    assert snap.container is not None
    assert snap.container.id == SHORT_ID
    assert snap.container.name == "odcli_pg_x_postgres"
    assert snap.container.image == "postgres:16"
    assert snap.container.pid == 9124
    assert snap.container.pid_scope is PidScope.HOST
    assert snap.metrics is not None
    assert snap.metrics.cpu_percent == 4.2
    assert snap.metrics.memory_usage_bytes == 512 * 1024**2
    assert snap.metrics.memory_limit_bytes == 1024**3
    assert snap.metrics.volume_usage_bytes is None
    assert snap.metrics.sampled_at == snap.sampled_at


@pytest.mark.unit
def test_resource_snapshot_healthy_darwin_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[_healthy_inspect()],
        stats_lines=[_healthy_stats()],
    )
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.container is not None
    assert snap.container.pid_scope is PidScope.DOCKER_VM


@pytest.mark.unit
def test_resource_snapshot_inspect_failed() -> None:
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_rc=1,
        stats_lines=[_healthy_stats()],
    )
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.unavailability_reason == "inspect_failed"
    assert snap.container is None
    assert snap.metrics is None


@pytest.mark.unit
def test_resource_snapshot_stats_failed() -> None:
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[_healthy_inspect()],
        stats_rc=1,
    )
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.unavailability_reason == "stats_failed"
    assert snap.container is not None
    assert snap.container.pid == 9124
    assert snap.metrics is None


@pytest.mark.unit
def test_resource_snapshot_no_secrets_exposed() -> None:
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[_healthy_inspect()],
        stats_lines=[_healthy_stats()],
    )
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.container is not None
    assert snap.container.name is not None
    assert "PASSWORD" not in snap.container.name
    assert snap.container.image == "postgres:16"
    # No env vars, no password values anywhere on the snapshot struct.
    blob = repr(snap)
    assert "POSTGRES_PASSWORD_FILE" not in blob
    assert "postgres_password" not in blob


@pytest.mark.unit
def test_inspect_batch_one_call_for_multiple_ids() -> None:
    id1 = "a1b2c3d4e5f6" + "0" * 20
    id2 = "f6e5d4c3b2a1" + "0" * 20
    runner = FakeDockerRunner(
        inspect_payload=[
            {"Id": id1, "Name": "/c1", "Config": {"Image": "postgres:16"}, "State": {"Pid": 1}},
            {"Id": id2, "Name": "/c2", "Config": {"Image": "postgres:16"}, "State": {"Pid": 2}},
        ],
    )
    result = inspect_containers((id1, id2), runner=runner)
    assert result[id1] is not None
    assert result[id2] is not None
    inspect_calls = [c for c in runner.calls if c[:2] == ["docker", "inspect"]]
    assert len(inspect_calls) == 1
    assert id1 in inspect_calls[0] and id2 in inspect_calls[0]


@pytest.mark.unit
def test_stats_batch_one_call_for_multiple_ids() -> None:
    id1 = "a1b2c3d4e5f6" + "0" * 20
    id2 = "f6e5d4c3b2a1" + "0" * 20
    runner = FakeDockerRunner(
        stats_lines=[
            {"container": id1, "CPUPerc": "1.00%", "MemUsage": "1MiB / 2MiB"},
            {"container": id2, "CPUPerc": "2.00%", "MemUsage": "3MiB / 4MiB"},
        ],
    )
    result = stats_containers((id1, id2), runner=runner)
    assert result[id1] is not None
    assert result[id2] is not None
    stats_calls = [c for c in runner.calls if c[:2] == ["docker", "stats"]]
    assert len(stats_calls) == 1
    assert id1 in stats_calls[0] and id2 in stats_calls[0]


@pytest.mark.unit
def test_inspect_batch_partial_failure_does_not_block() -> None:
    id1 = "a1b2c3d4e5f6" + "0" * 20
    id2 = "f6e5d4c3b2a1" + "0" * 20
    # Only id1 returned in payload; id2 resolves to None but doesn't raise.
    runner = FakeDockerRunner(
        inspect_payload=[{"Id": id1, "Name": "/c1", "Config": {"Image": "i"}, "State": {"Pid": 1}}],
    )
    result = inspect_containers((id1, id2), runner=runner)
    assert result[id1] is not None
    assert result[id2] is None


@pytest.mark.unit
def test_inspect_batch_keeps_valid_partial_stdout_on_nonzero_exit() -> None:
    id1 = "a1b2c3d4e5f6" + "0" * 20
    id2 = "f6e5d4c3b2a1" + "0" * 20
    runner = FakeDockerRunner(
        inspect_rc=1,
        inspect_payload=[{"Id": id1, "Name": "/c1", "Config": {"Image": "i"}, "State": {"Pid": 1}}],
    )
    result = inspect_containers((id1, id2), runner=runner)
    assert result[id1] is not None
    assert result[id2] is None


@pytest.mark.unit
def test_stats_batch_keeps_valid_partial_stdout_on_nonzero_exit() -> None:
    id1 = "a1b2c3d4e5f6" + "0" * 20
    id2 = "f6e5d4c3b2a1" + "0" * 20
    runner = FakeDockerRunner(
        stats_rc=1,
        stats_lines=[{"container": id1, "CPUPerc": "1%", "MemUsage": "1MiB / 2MiB"}],
    )
    result = stats_containers((id1, id2), runner=runner)
    assert result[id1] is not None
    assert result[id2] is None


@pytest.mark.unit
def test_standalone_collection_has_no_global_cache() -> None:
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[_healthy_inspect()],
        stats_lines=[_healthy_stats()],
    )
    cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    inspect_calls = [c for c in runner.calls if c[:2] == ["docker", "inspect"]]
    stats_calls = [c for c in runner.calls if c[:2] == ["docker", "stats"]]
    assert len(inspect_calls) == 2
    assert len(stats_calls) == 2


@pytest.mark.unit
def test_standalone_collection_needs_no_test_only_cache_reset() -> None:
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[_healthy_inspect()],
        stats_lines=[_healthy_stats()],
    )
    for _ in range(2):
        cluster_resources.cluster_resource_snapshot(
            compose_file=Path("/tmp/compose.yaml"),
            compose_project_name="odcli_pg_x",
            service="postgres",
            runner=runner,
            state=PostgresClusterState.HEALTHY,
        )
    inspect_calls = [c for c in runner.calls if c[:2] == ["docker", "inspect"]]
    assert len(inspect_calls) == 2


@pytest.mark.unit
def test_pid_scope_unavailable_when_no_pid() -> None:
    runner = FakeDockerRunner(
        ps_rows=[{"Service": "postgres", "ID": FULL_ID}],
        inspect_payload=[
            {"Id": FULL_ID, "Name": "/c", "Config": {"Image": "i"}, "State": {"Pid": 0}}
        ],
        stats_lines=[_healthy_stats()],
    )
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.HEALTHY,
    )
    assert snap.container is not None
    assert snap.container.pid is None
    assert snap.container.pid_scope is PidScope.UNAVAILABLE


@pytest.mark.unit
def test_snapshot_is_cluster_resource_snapshot_type() -> None:
    runner = FakeDockerRunner(ps_rows=[])
    snap = cluster_resources.cluster_resource_snapshot(
        compose_file=Path("/tmp/compose.yaml"),
        compose_project_name="odcli_pg_x",
        service="postgres",
        runner=runner,
        state=PostgresClusterState.STOPPED,
    )
    assert isinstance(snap, ClusterResourceSnapshot)
