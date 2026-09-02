from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import (
    PostgresClusterError,
    PostgresClusterNotOwnedError,
    PostgresClusterStartError,
    PostgresClusterStopError,
    PostgresClusterTimeoutError,
    PostgresClusterUnhealthyError,
    PostgresClusterUnreachableError,
    PostgresComposeInvalidError,
    PostgresComposeUnavailableError,
    PostgresImageNotTrustedError,
    PostgresPortCollisionError,
)
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.pg.server import ServerSummary
from odoo_instance_sdk.internal.postgres_compose import ComposeRunner, SubprocessComposeRunner
from odoo_instance_sdk.internal.proc import ProcessResult, RecordingExecutor
from odoo_instance_sdk.models import PostgresClusterState
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig
from odoo_instance_sdk.resources.postgres import PostgresCluster


class FakeComposeRunner(ComposeRunner):
    """Records invocations; returns scripted results."""

    requires_docker = False

    def __init__(
        self,
        *,
        ps_rows: list[dict[str, object]] | None = None,
        health_rc: int = 0,
        up_rc: int = 0,
        stop_rc: int = 0,
        config_rc: int = 0,
        ps_rc: int = 0,
    ) -> None:
        self.calls: list[list[str]] = []
        self.timeouts: list[float | None] = []
        self._ps_rows = ps_rows or []
        self._health_rc = health_rc
        self._up_rc = up_rc
        self._stop_rc = stop_rc
        self._config_rc = config_rc
        self._ps_rc = ps_rc

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        self.timeouts.append(timeout)
        joined = " ".join(args)
        if " image inspect " in joined:
            return subprocess.CompletedProcess(
                args, 0, "docker.io/library/postgres@sha256:" + "a" * 64, ""
            )
        if " image pull " in joined:
            return subprocess.CompletedProcess(args, 0, "", "")
        if " config " in joined:
            return subprocess.CompletedProcess(
                args, self._config_rc, "", "" if self._config_rc == 0 else "bad"
            )
        if " up " in joined:
            return subprocess.CompletedProcess(
                args, self._up_rc, "", "" if self._up_rc == 0 else "up fail"
            )
        if " stop " in joined:
            return subprocess.CompletedProcess(
                args, self._stop_rc, "", "" if self._stop_rc == 0 else "stop fail"
            )
        if " ps " in joined:
            return subprocess.CompletedProcess(args, self._ps_rc, _rows_to_jsonl(self._ps_rows), "")
        if " exec " in joined:
            return subprocess.CompletedProcess(
                args, self._health_rc, "ok" if self._health_rc == 0 else "fail", ""
            )
        return subprocess.CompletedProcess(args, 0, "", "")


class StartingComposeRunner(FakeComposeRunner):
    """Deterministically transitions STOPPED -> HEALTHY after compose up."""

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = super().run(args, cwd=cwd, timeout=timeout)
        if " up " in f" {' '.join(args)} ":
            self._ps_rows = [{"Name": "postgres"}]
            self._health_rc = 0
        return result


def _rows_to_jsonl(rows: list[dict[str, object]]) -> str:
    import json

    return "\n".join(json.dumps(r) for r in rows)


def _write_compose_project(
    tmp_path: Path,
    *,
    mode: str = "compose",
    image: str | None = "pgvector/pgvector:pg16",
    port: int | None = 5468,
    user: str | None = "odoo",
    source_config: Path | None = None,
) -> Path:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    cfg = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        source_config=source_config,
        postgres=PostgresProjectConfig(mode=mode, image=image, port=port, user=user)  # type: ignore[arg-type]
        if mode == "compose"
        else None,
    )
    (manifest_dir / "project.toml").write_text(cfg.to_manifest())
    return tmp_path


def _write_source_config(
    tmp_path: Path, *, db_host: str = "127.0.0.1", db_port: int = 5432
) -> Path:
    p = tmp_path / "odoo.conf"
    p.write_text(f"[options]\ndb_host = {db_host}\ndb_port = {db_port}\ndb_user = alice\n")
    return p


@pytest.mark.unit
def test_from_project_compose_mode(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    assert cluster.mode == "compose"
    assert cluster.owned is True
    assert cluster.endpoint == "127.0.0.1:5468"
    assert "127.0.0.1:5468" in repr(cluster)
    assert "password" not in repr(cluster).lower()


@pytest.mark.unit
def test_from_project_external_reads_source_config(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="db.local", db_port=5433)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    assert cluster.mode == "external"
    assert cluster.owned is False
    assert "db.local" in cluster.endpoint
    assert "5433" in cluster.endpoint


@pytest.mark.unit
def test_from_project_external_without_source_config_raises(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path, mode="external")
    with pytest.raises(PostgresClusterError, match="requires source_config"):
        PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())


@pytest.mark.unit
def test_legacy_manifest_treated_as_external(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    cfg_path = _write_source_config(tmp_path)
    (manifest_dir / "project.toml").write_text(
        f'[project]\nodoo_bin = "/opt/odoo/odoo-bin"\nsource_config = "{cfg_path}"\n'
    )
    cluster = PostgresCluster.from_project(tmp_path, compose_runner=FakeComposeRunner())
    assert cluster.mode == "external"
    assert cluster.owned is False


@pytest.mark.unit
def test_status_external_reachable_is_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.probe_address",
        lambda host, port: AddressState.OCCUPIED,
    )
    assert cluster.status() is PostgresClusterState.HEALTHY


@pytest.mark.unit
def test_status_external_does_not_invoke_docker(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    fake = FakeComposeRunner()
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster.status()
    assert fake.calls == []


@pytest.mark.unit
def test_status_compose_stopped_when_no_containers(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[])
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    state = cluster.status()
    assert state is PostgresClusterState.STOPPED


@pytest.mark.unit
def test_status_compose_stopped_when_container_is_exited(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres", "State": "exited"}], health_rc=1)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")

    assert cluster.status() is PostgresClusterState.STOPPED
    assert not any(" exec " in f" {' '.join(call)} " for call in fake.calls)


@pytest.mark.unit
def test_status_compose_healthy_when_health_rc_zero(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], health_rc=0)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    state = cluster.status()
    assert state is PostgresClusterState.HEALTHY


@pytest.mark.unit
def test_status_command_consumes_the_inspected_process_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root, compose_runner=SubprocessComposeRunner())
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.docker_available", lambda: True)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.pg.builder.shutil.which", lambda _name: "/usr/bin/psql"
    )

    def result(stdout: str) -> ProcessResult:
        return ProcessResult(
            argv=(),
            returncode=0,
            stdout=stdout,
            stderr="",
            duration=0.0,
            cwd=None,
            environment=(),
        )

    executor = RecordingExecutor(
        results={
            "postgres.status.ps": result('{"Name":"postgres","State":"running"}\n'),
            "postgres.status.health": result("ok"),
            "postgres.status.server-summary.0": result(
                '{"version":"16","postmaster_started_at":"2026-01-01T00:00:00Z",'
                '"uptime_seconds":42,"connections_total":2,"connections_active":1,'
                '"connections_idle":1,"max_connections":100,"connectable_databases":1}'
            ),
        }
    )
    summaries: list[ServerSummary] = []
    command = cluster.status_command(executor=executor, server_summary_sink=summaries.append)

    assert command.run() is PostgresClusterState.HEALTHY
    assert tuple(step.step_id for step in command.plan.process_steps) == (
        "postgres.status.ps",
        "postgres.status.health",
        "postgres.status.server-summary.0",
        "postgres.status.server-summary.1",
    )
    assert tuple(step.step_id for step in executor.executed) == (
        "postgres.status.ps",
        "postgres.status.health",
        "postgres.status.server-summary.0",
    )
    assert command.plan.observations == (
        {
            "kind": "deadline-bound-attempt",
            "scope": "postgres.status.server-summary",
            "step_ids": [
                "postgres.status.server-summary.0",
                "postgres.status.server-summary.1",
            ],
            "budget_seconds": 10.0,
        },
    )
    assert len(summaries) == 1
    assert summaries[0].server is not None


@pytest.mark.unit
def test_status_compose_starting_when_health_rc_nonzero(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], health_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    state = cluster.status()
    assert state is PostgresClusterState.STARTING


@pytest.mark.unit
def test_status_compose_unhealthy_when_health_label_unhealthy(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres", "Health": "unhealthy"}], health_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    state = cluster.status()
    assert state is PostgresClusterState.UNHEALTHY


@pytest.mark.unit
def test_status_compose_unknown_when_ps_fails(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rc=1)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    state = cluster.status()
    assert state is PostgresClusterState.UNKNOWN


@pytest.mark.unit
def test_status_compose_unknown_when_docker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root)
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.docker_available", lambda: False)
    state = cluster.status()
    assert state is PostgresClusterState.UNKNOWN


@pytest.mark.unit
def test_ensure_running_external_unreachable_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.probe_address", lambda host, port: AddressState.FREE
    )
    with pytest.raises(PostgresClusterUnreachableError):
        cluster.ensure_running(timeout=1.0)


@pytest.mark.unit
def test_ensure_running_external_healthy_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    fake = FakeComposeRunner()
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.postgres.probe_address",
        lambda host, port: (
            __import__(
                "odoo_instance_sdk.internal.address", fromlist=["AddressState"]
            ).AddressState.OCCUPIED
        ),
    )
    cluster.ensure_running(timeout=1.0)
    assert fake.calls == []  # no Docker


@pytest.mark.unit
def test_stop_external_raises_not_owned(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path, db_host="127.0.0.1", db_port=5432)
    root = _write_compose_project(tmp_path, mode="external", source_config=cfg_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    with pytest.raises(PostgresClusterNotOwnedError):
        cluster.stop(timeout=1.0)


@pytest.mark.unit
def test_stop_compose_when_no_artifacts_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], stop_rc=0)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    # No compose.yaml yet → stop returns without invoking docker.
    cluster.stop(timeout=1.0)
    assert fake.calls == []


@pytest.mark.unit
def test_stop_compose_invokes_compose_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], stop_rc=0)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    # Simulate artifacts existing by pre-creating compose.yaml.
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.docker_available", lambda: True)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services:\n  postgres:\n    image: x\n")
    cluster.stop(timeout=5.0)
    assert any(" stop " in " ".join(c) for c in fake.calls)


@pytest.mark.unit
def test_stop_maps_failure_and_forwards_bounded_timeout(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], stop_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services: {}\n")
    with pytest.raises(PostgresClusterStopError):
        cluster.stop(timeout=1.0)
    stop_index = next(
        index for index, call in enumerate(fake.calls) if " stop " in f" {' '.join(call)} "
    )
    assert fake.timeouts[stop_index] is not None


@pytest.mark.unit
def test_stop_existing_stopped_cluster_is_noop(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[])
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster.compose_file.parent.mkdir(parents=True, exist_ok=True)
    cluster.compose_file.write_text("services: {}\n")
    cluster.stop(timeout=1.0)
    assert not any(" stop " in f" {' '.join(call)} " for call in fake.calls)


@pytest.mark.unit
def test_stop_lock_conflict_maps_to_typed_timeout(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.locks import exclusive_lock, postgres_cluster_lock_path

    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    cluster.compose_file.parent.mkdir(parents=True, exist_ok=True)
    cluster.compose_file.write_text("services: {}\n")
    with (
        exclusive_lock(postgres_cluster_lock_path(cluster._project_id)),
        pytest.raises(PostgresClusterTimeoutError),
    ):
        cluster.stop(timeout=0.01)


@pytest.mark.unit
def test_compound_compose_deadline_decreases_between_subcommands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.internal.postgres_compose import derive_state, resolve_image_digest

    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], health_rc=0)
    ticks = iter([0.0, 0.1, 0.4, 1.0, 1.2, 1.6])
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.time.monotonic", lambda: next(ticks)
    )
    resolve_image_digest(fake, "postgres:16", timeout=2.0)
    image_timeouts = [timeout for timeout in fake.timeouts if timeout is not None]
    assert image_timeouts[1] < image_timeouts[0]
    fake.calls.clear()
    fake.timeouts.clear()
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n")
    derive_state(fake, compose, "project", user="odoo", timeout=2.0)
    state_timeouts = [timeout for timeout in fake.timeouts if timeout is not None]
    assert state_timeouts[1] < state_timeouts[0]


@pytest.mark.unit
def test_ensure_running_compose_unavailable_docker_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.docker_available", lambda: False
    )
    with pytest.raises(PostgresComposeUnavailableError):
        cluster.ensure_running(timeout=1.0)


@pytest.mark.unit
def test_ensure_running_compose_invalid_config_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(config_rc=1)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster.approve_image("docker.io/library/postgres@sha256:" + "a" * 64)
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.docker_available", lambda: True)
    with pytest.raises(PostgresComposeInvalidError):
        cluster.ensure_running(timeout=1.0)
    assert not cluster._compose_file().is_file()


@pytest.mark.unit
def test_ensure_running_stopped_transitions_to_healthy_once(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = StartingComposeRunner(ps_rows=[], health_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    digest = "docker.io/library/postgres@sha256:" + "a" * 64
    cluster.approve_image(digest)
    cluster.ensure_running(timeout=2.0)
    up_calls = [call for call in fake.calls if " up " in f" {' '.join(call)} "]
    assert len(up_calls) == 1
    assert up_calls[0][-3:] == ["up", "--detach", "--wait"]
    assert cluster.status() is PostgresClusterState.HEALTHY


@pytest.mark.unit
def test_compose_up_maps_timeout_nonzero_and_port_collision(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.postgres_compose import compose_up

    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n")

    class TimeoutRunner(FakeComposeRunner):
        def run(self, args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(args, 0.1)

    with pytest.raises(PostgresClusterTimeoutError):
        compose_up(TimeoutRunner(), compose, "project", timeout=0.1)
    with pytest.raises(PostgresClusterStartError):
        compose_up(FakeComposeRunner(up_rc=2), compose, "project", timeout=1.0)

    class CollisionRunner(FakeComposeRunner):
        def run(self, args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 1, "", "Bind: address already in use")

    with pytest.raises(PostgresPortCollisionError):
        compose_up(CollisionRunner(), compose, "project", timeout=1.0)


@pytest.mark.unit
def test_lifecycle_lock_waits_then_acquires_and_times_out(tmp_path: Path) -> None:
    from odoo_instance_sdk.exceptions import LockConflictError
    from odoo_instance_sdk.internal.locks import exclusive_lock, exclusive_lock_until

    lock_path = tmp_path / "postgres.lock"
    acquired = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with exclusive_lock(lock_path):
            acquired.set()
            release.wait(1.0)

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(1.0)
    with pytest.raises(LockConflictError), exclusive_lock_until(lock_path, time.monotonic() + 0.01):
        pass
    release.set()
    thread.join(timeout=1.0)
    with exclusive_lock_until(lock_path, time.monotonic() + 1.0):
        pass


@pytest.mark.unit
def test_concurrent_ensure_rechecks_after_lock_and_starts_once(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = StartingComposeRunner(ps_rows=[], health_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster.approve_image("docker.io/library/postgres@sha256:" + "a" * 64)
    fake.calls.clear()
    fake.timeouts.clear()
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def ensure() -> None:
        try:
            barrier.wait(timeout=1.0)
            cluster.ensure_running(timeout=2.0)
        except BaseException as exc:  # retained to report a thread failure to the test thread
            failures.append(exc)

    first = threading.Thread(target=ensure)
    second = threading.Thread(target=ensure)
    first.start()
    second.start()
    first.join(timeout=3.0)
    second.join(timeout=3.0)
    assert failures == []
    assert sum(" up " in f" {' '.join(call)} " for call in fake.calls) == 1
    assert all(timeout is not None and timeout > 0 for timeout in fake.timeouts)


@pytest.mark.unit
def test_ensure_poll_timeout_is_typed(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[], health_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster.approve_image("docker.io/library/postgres@sha256:" + "a" * 64)
    with pytest.raises(PostgresClusterTimeoutError):
        cluster.ensure_running(timeout=0.1)


@pytest.mark.unit
def test_ensure_unhealthy_before_up_is_typed(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres", "Health": "unhealthy"}], health_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster._compose_file().parent.mkdir(parents=True, exist_ok=True)
    cluster._compose_file().write_text("services: {}\n")
    cluster.approve_image("docker.io/library/postgres@sha256:" + "a" * 64)
    with pytest.raises(PostgresClusterUnhealthyError):
        cluster.ensure_running(timeout=1.0)
    assert not any(" up " in f" {' '.join(call)} " for call in fake.calls)


@pytest.mark.unit
def test_ensure_unhealthy_after_up_is_typed(tmp_path: Path) -> None:
    class UnhealthyAfterUp(FakeComposeRunner):
        def run(
            self,
            args: Sequence[str],
            *,
            cwd: Path | None = None,
            timeout: float | None = None,
        ) -> subprocess.CompletedProcess[str]:
            result = super().run(args, cwd=cwd, timeout=timeout)
            if " up " in f" {' '.join(args)} ":
                self._ps_rows = [{"Name": "postgres", "Health": "unhealthy"}]
                self._health_rc = 2
            return result

    root = _write_compose_project(tmp_path)
    fake = UnhealthyAfterUp(ps_rows=[])
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster.approve_image("docker.io/library/postgres@sha256:" + "a" * 64)
    with pytest.raises(PostgresClusterUnhealthyError):
        cluster.ensure_running(timeout=1.0)


@pytest.mark.unit
def test_lifecycle_command_budgets_decrease_with_controlled_monotonic_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_compose_project(tmp_path)
    fake = StartingComposeRunner(ps_rows=[], health_rc=2)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    cluster.approve_image("docker.io/library/postgres@sha256:" + "a" * 64)
    fake.timeouts.clear()
    tick = iter(0.01 * number for number in range(1, 200))
    monkeypatch.setattr("odoo_instance_sdk.resources.postgres.time.monotonic", lambda: next(tick))
    cluster.ensure_running(timeout=2.0)
    budgets = [timeout for timeout in fake.timeouts if timeout is not None]
    assert budgets == sorted(budgets, reverse=True)


@pytest.mark.unit
def test_image_approval_persists_reloads_and_rejects_corruption(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    digest = "docker.io/library/postgres@sha256:" + "a" * 64
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    cluster.approve_image(digest)
    trust = cluster._trust_file()
    assert trust.stat().st_mode & 0o777 == 0o600
    reloaded = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    assert reloaded._require_trusted_image(1.0) == digest
    reloaded.approve_image(digest)
    assert reloaded._require_trusted_image(1.0) == digest
    trust.write_text("not-json")
    with pytest.raises(PostgresImageNotTrustedError):
        reloaded._require_trusted_image(1.0)


@pytest.mark.unit
def test_image_approval_rejects_external_and_mismatched_digest(tmp_path: Path) -> None:
    cfg_path = _write_source_config(tmp_path)
    external = PostgresCluster.from_project(
        _write_compose_project(tmp_path, mode="external", source_config=cfg_path),
        compose_runner=FakeComposeRunner(),
    )
    with pytest.raises(PostgresClusterNotOwnedError):
        external.approve_image("docker.io/library/postgres@sha256:" + "a" * 64)


@pytest.mark.unit
def test_changed_resolved_digest_invalidates_prior_approval(tmp_path: Path) -> None:
    class DriftRunner(FakeComposeRunner):
        drift = False

        def run(
            self,
            args: Sequence[str],
            *,
            cwd: Path | None = None,
            timeout: float | None = None,
        ) -> subprocess.CompletedProcess[str]:
            result = super().run(args, cwd=cwd, timeout=timeout)
            if " image inspect " in f" {' '.join(args)} " and self.drift:
                return subprocess.CompletedProcess(
                    args, 0, "docker.io/library/postgres@sha256:" + "b" * 64, ""
                )
            return result

    root = _write_compose_project(tmp_path)
    runner = DriftRunner()
    cluster = PostgresCluster.from_project(root, compose_runner=runner)
    cluster.approve_image("docker.io/library/postgres@sha256:" + "a" * 64)
    runner.drift = True
    with pytest.raises(PostgresImageNotTrustedError, match="changed"):
        cluster._require_trusted_image(1.0)


@pytest.mark.unit
def test_approve_mismatched_digest_does_not_publish_trust(tmp_path: Path) -> None:
    cluster = PostgresCluster.from_project(
        _write_compose_project(tmp_path), compose_runner=FakeComposeRunner()
    )
    with pytest.raises(PostgresImageNotTrustedError, match="does not match"):
        cluster.approve_image("docker.io/library/postgres@sha256:" + "b" * 64)
    assert not cluster._trust_file().exists()


@pytest.mark.unit
def test_ensure_running_requires_local_image_approval(tmp_path: Path) -> None:
    fake = FakeComposeRunner()
    cluster = PostgresCluster.from_project(_write_compose_project(tmp_path), compose_runner=fake)
    with pytest.raises(PostgresImageNotTrustedError, match="approve-image"):
        cluster.ensure_running(timeout=1.0)
    assert fake.calls == []


@pytest.mark.unit
def test_oci_digest_accepts_registry_port() -> None:
    from odoo_instance_sdk.internal.postgres_compose import is_oci_digest

    assert is_oci_digest("registry.example:5000/team/postgres@sha256:" + "a" * 64)


@pytest.mark.unit
def test_existing_password_symlink_is_rejected(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.postgres_compose import ensure_password_file

    target = tmp_path / "target"
    target.write_text("secret\n")
    password = tmp_path / "postgres-password"
    password.symlink_to(target)
    with pytest.raises(PostgresComposeInvalidError, match="regular file"):
        ensure_password_file(password)


@pytest.mark.unit
def test_password_file_mode_0600(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_compose_project(tmp_path)
    fake = FakeComposeRunner(ps_rows=[{"Name": "postgres"}], health_rc=0, config_rc=0)
    cluster = PostgresCluster.from_project(root, compose_runner=fake)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.postgres_compose.docker_available", lambda: True
    )
    cluster._ensure_artifacts("docker.io/library/postgres@sha256:" + "a" * 64)
    pw_path = cluster._password_file()
    assert pw_path.is_file()
    mode = pw_path.stat().st_mode & 0o777
    assert mode == 0o600, f"password file mode {oct(mode)}"
    compose_mode = cluster._compose_file().stat().st_mode & 0o777
    assert compose_mode == 0o600, f"compose file mode {oct(compose_mode)}"


@pytest.mark.unit
def test_password_file_not_overwritten(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    cluster._compose_dir().mkdir(parents=True, exist_ok=True)
    pw_path = cluster._password_file()
    pw_path.write_text("existing-password\n")
    os.chmod(pw_path, 0o600)
    from odoo_instance_sdk.internal.postgres_compose import ensure_password_file

    content = ensure_password_file(pw_path)
    assert content == "existing-password"
    assert pw_path.read_text().strip() == "existing-password"


@pytest.mark.unit
def test_render_compose_yaml_minimal(tmp_path: Path) -> None:
    from odoo_instance_sdk.internal.postgres_compose import render_compose_yaml

    content = render_compose_yaml(
        image="pgvector/pgvector:pg16",
        port=5468,
        user="odoo",
        project_id="proj_12345678",
        password_file="/data/projects/proj_12345678/postgres/postgres-password",
    )
    assert "image: pgvector/pgvector:pg16" in content
    assert "127.0.0.1:5468:5432" in content
    assert "POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password" in content
    assert "pg_isready" in content
    assert "container_name" not in content
    assert "build:" not in content
    assert "extends:" not in content


@pytest.mark.unit
def test_render_compose_rejects_unsafe_image() -> None:
    from odoo_instance_sdk.internal.postgres_compose import render_compose_yaml

    with pytest.raises(PostgresComposeInvalidError):
        render_compose_yaml(
            image="image'; rm -rf /",
            port=5468,
            user="odoo",
            project_id="x",
            password_file="/p",
        )


@pytest.mark.unit
def test_render_compose_rejects_unsafe_user() -> None:
    from odoo_instance_sdk.internal.postgres_compose import render_compose_yaml

    with pytest.raises(PostgresComposeInvalidError):
        render_compose_yaml(
            image="pg",
            port=5468,
            user="user; rm -rf /",
            project_id="x",
            password_file="/p",
        )


@pytest.mark.unit
def test_diagnostic_dict_is_redacted(tmp_path: Path) -> None:
    root = _write_compose_project(tmp_path)
    cluster = PostgresCluster.from_project(root, compose_runner=FakeComposeRunner())
    diag = dict(cluster.to_diagnostic_dict())
    assert diag["mode"] == "compose"
    assert diag["owned"] is True
    assert "password" not in str(diag).lower()
