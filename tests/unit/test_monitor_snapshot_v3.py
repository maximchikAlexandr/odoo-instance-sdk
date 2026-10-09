from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from odoo_instance_sdk.internal.process_metrics import (
    CpuPoint,
    ProcessTreeResult,
    collect_process_tree,
)
from odoo_instance_sdk.models import Snapshot, SnapshotRequest
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from tests.unit.monitor_support import (
    FakeDockerProvider,
    FakeGitProvider,
    FakePostgresCluster,
    FakeProcessProvider,
    make_catalog,
    make_env,
    patch_from_project,
    seed_env,
    seed_runtime,
)


def test_unselected_sections_do_not_probe_or_cache(tmp_path: Path, monkeypatch: object) -> None:
    catalog = make_catalog(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    env_id = str(uuid.uuid4())
    seed_env(catalog, make_env(env_id, worktree_path=str(worktree)))
    seed_runtime(catalog, env_id)
    catalog.close()

    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))  # type: ignore[arg-type]
    git = FakeGitProvider()
    docker = FakeDockerProvider()
    process = FakeProcessProvider(
        result=ProcessTreeResult(child_pids=(), process_count=1, cpu_percent=None, memory_bytes=1)
    )
    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        git_provider=git,
        docker_provider=docker,
        process_provider=process,
    )

    snapshot = monitor.snapshot(request=SnapshotRequest(sections=("catalogue", "runtime")))

    assert git.calls == 0
    assert docker.calls == 0
    assert process.calls == 1
    assert monitor._git_cache == {}
    assert monitor._storage_cache == {}
    assert monitor._cluster_resource_cache == {}
    assert snapshot.requested_sections == ("catalogue", "runtime")
    assert snapshot.unknown_sections == ()
    assert snapshot.observed_at is not None


def test_snapshot_observation_marks_unavailable_runtime(
    tmp_path: Path, monkeypatch: object
) -> None:
    catalog = make_catalog(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    env_id = str(uuid.uuid4())
    seed_env(catalog, make_env(env_id, worktree_path=str(worktree)))
    seed_runtime(catalog, env_id)
    catalog.close()

    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))  # type: ignore[arg-type]
    snapshot = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        process_provider=FakeProcessProvider(),
    ).snapshot(request=SnapshotRequest(sections=("catalogue", "runtime")))

    assert snapshot.environments[0].runtime.state.value == "stopped"
    assert snapshot.unknown_sections == ("runtime",)
    assert snapshot.observation is not None
    runtime_observation = next(
        item for item in snapshot.observation.sections if item.section == "runtime"
    )
    assert runtime_observation.complete is False
    assert runtime_observation.reason == "runtime observation unavailable"


def test_snapshot_batch_sections_share_one_observation_boundary(
    tmp_path: Path, monkeypatch: object
) -> None:
    catalog = make_catalog(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    seed_env(catalog, make_env(str(uuid.uuid4()), worktree_path=str(worktree)))
    seed_env(
        catalog,
        make_env(str(uuid.uuid4()), branch="feature/batch", worktree_path=str(worktree / "batch")),
    )
    catalog.close()

    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))  # type: ignore[arg-type]
    snapshot = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        git_provider=FakeGitProvider(),
        process_provider=FakeProcessProvider(
            result=ProcessTreeResult(
                child_pids=(), process_count=1, cpu_percent=None, memory_bytes=1
            )
        ),
    ).snapshot(request=SnapshotRequest(sections=("catalogue", "runtime", "git")))

    assert snapshot.observation is not None
    assert snapshot.observation.unknown_sections == ()
    assert {section.observed_at for section in snapshot.observation.sections} == {
        snapshot.observation.observed_at
    }


def test_watch_preserves_each_snapshot_observation_boundary(
    tmp_path: Path, monkeypatch: object
) -> None:
    catalog = make_catalog(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    seed_env(catalog, make_env(str(uuid.uuid4()), worktree_path=str(worktree)))
    catalog.close()

    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))  # type: ignore[arg-type]
    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")

    async def take_two() -> list[Snapshot]:
        snapshots: list[Snapshot] = []
        async for snapshot in monitor.watch(interval=0.1):
            snapshots.append(snapshot)
            if len(snapshots) == 2:
                break
        return snapshots

    snapshots = asyncio.run(take_two())
    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert snapshot.observation is not None
        assert {section.observed_at for section in snapshot.observation.sections} == {
            snapshot.observation.observed_at
        }


def test_snapshot_observation_marks_unavailable_docker(tmp_path: Path, monkeypatch: object) -> None:
    catalog = make_catalog(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    seed_env(catalog, make_env(str(uuid.uuid4()), worktree_path=str(worktree)))
    catalog.close()

    patch_from_project(monkeypatch, FakePostgresCluster(mode="compose"))  # type: ignore[arg-type]
    docker = FakeDockerProvider()
    snapshot = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", docker_provider=docker
    ).snapshot(request=SnapshotRequest(sections=("catalogue", "docker")))

    assert docker.calls == 1
    assert snapshot.unknown_sections == ("docker",)
    assert snapshot.observation is not None
    docker_observation = next(
        item for item in snapshot.observation.sections if item.section == "docker"
    )
    assert docker_observation.complete is False
    assert docker_observation.reason == "stats_failed"


def test_cpu_sample_carries_identity_and_rejects_pid_reuse(monkeypatch: object) -> None:
    from tests.unit.test_process_metrics import FakeProcess, _install_psutil, _make_psutil

    monkeypatch.setattr("sys.platform", "linux")  # type: ignore[attr-defined]
    process = FakeProcess(pid=7, create_time=11.0, cpu_times=(2.0, 3.0), rss=1)
    fake = _make_psutil(root={"pid": 7, "instance": process})
    _install_psutil(monkeypatch, fake)  # type: ignore[arg-type]

    first = collect_process_tree(7, 11.0, prev_cpu_point=None)
    assert first is not None
    result, point = first
    assert result.root_pid == 7
    assert result.create_time == 11.0
    assert result.cpu_seconds == 5.0
    assert result.sampled_at is not None

    prior = CpuPoint(
        times_cpu=1.0,
        timestamp=point.timestamp - 1.0,
        pid=7,
        create_time=12.0,
    )
    second = collect_process_tree(7, 11.0, prev_cpu_point=prior)
    assert second is not None
    assert second[0].cpu_percent is None
