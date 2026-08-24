from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import httpx
import msgspec
import pytest

from odoo_instance_sdk.exceptions import MonitorError, MonitorExtrasMissingError
from odoo_instance_sdk.internal.process_metrics import CpuPoint, ProcessTreeResult
from odoo_instance_sdk.models import (
    ClusterContainer,
    ClusterMetrics,
    ClusterResourceSnapshot,
    GitActivity,
    GitActivityState,
    PidScope,
    PostgresClusterState,
    RuntimeState,
    Snapshot,
)
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.unit.monitor_support import (
    FakeDockerProvider,
    FakeGitProvider,
    FakePostgresCluster,
    FakeProcessProvider,
)
from tests.unit.monitor_support import (
    make_catalog as _make_catalog,
)
from tests.unit.monitor_support import (
    make_env as _make_env,
)
from tests.unit.monitor_support import (
    patch_from_project as _patch_from_project,
)
from tests.unit.monitor_support import (
    seed_env as _seed_env,
)
from tests.unit.monitor_support import (
    seed_runtime as _seed_runtime,
)
from tests.unit.monitor_support import (
    write_odoo_conf as _write_odoo_conf,
)


# --------------------------------------------------------------------- tests
@pytest.fixture(autouse=True)
def _inject_process_provider_for_core_monitor_tests(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Keep collector tests core-only; only the explicit extra contract imports psutil."""
    if request.node.name in {
        "test_default_monitor_requires_metrics_extra_even_for_empty_catalog",
        "test_psutil_missing_raises_extras_error",
    }:
        return
    original_init = EnvironmentMonitor.__init__

    def init(self: EnvironmentMonitor, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("process_provider", FakeProcessProvider())
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EnvironmentMonitor, "__init__", init)


def test_multi_project_discovery(tmp_path: Path) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    e2 = str(uuid.uuid4())
    _seed_env(catalog, _make_env(e1, git_common_dir="/repoA/.git", repository_root="/repoA"))
    _seed_env(catalog, _make_env(e2, git_common_dir="/repoB/.git", repository_root="/repoB"))
    catalog.close()

    monkeypatch = pytest.MonkeyPatch()
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    try:
        monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
        snap = monitor.snapshot()
    finally:
        monkeypatch.undo()

    assert len(snap.projects) == 2
    pids = [p.id for p in snap.projects]
    assert pids[0] < pids[1]
    assert all(p.id.startswith("project_") for p in snap.projects)
    assert {p.environment_count for p in snap.projects} == {1}
    assert len(snap.environments) == 2


def test_stopped_odoo_no_runtime_record(tmp_path: Path) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    monkeypatch = pytest.MonkeyPatch()
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    try:
        monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
        snap = monitor.snapshot()
    finally:
        monkeypatch.undo()

    env = snap.environments[0]
    assert env.runtime.state is RuntimeState.STOPPED
    assert env.runtime.root_pid is None
    assert env.runtime.cpu_percent is None
    assert env.runtime.rss_bytes is None


def test_running_odoo_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    _seed_runtime(catalog, e1)
    catalog.close()

    result = ProcessTreeResult(child_pids=(200,), process_count=2, cpu_percent=1.5, rss_bytes=4096)
    provider = FakeProcessProvider(result=result)
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    resp = httpx.Response(200, json={"status": "pass"})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", process_provider=provider
    )
    snap = monitor.snapshot()

    env = snap.environments[0]
    assert env.runtime.state is RuntimeState.READY
    assert env.runtime.root_pid == 12345
    assert env.runtime.process_count == 2
    assert env.runtime.cpu_percent == 1.5
    assert env.runtime.rss_bytes == 4096
    assert env.runtime.http_url == "http://127.0.0.1:8069"


def test_running_odoo_not_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    _seed_runtime(catalog, e1)
    catalog.close()

    result = ProcessTreeResult(child_pids=(), process_count=1, cpu_percent=0.0, rss_bytes=2048)
    provider = FakeProcessProvider(result=result)
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    resp = httpx.Response(503, json={"status": "fail"})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", process_provider=provider
    )
    snap = monitor.snapshot()

    env = snap.environments[0]
    assert env.runtime.state is RuntimeState.NOT_READY
    assert env.runtime.root_pid == 12345
    assert env.runtime.rss_bytes == 2048


def test_pid_reuse_returns_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    _seed_runtime(catalog, e1)
    catalog.close()

    provider = FakeProcessProvider(result=None)
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", process_provider=provider
    )
    snap = monitor.snapshot()

    env = snap.environments[0]
    assert env.runtime.state is RuntimeState.STOPPED


def test_compose_cluster_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    crs = ClusterResourceSnapshot(
        container=ClusterContainer(
            id="abc123def456", name="pg-1", image="pg:16", pid=42, pid_scope=PidScope.HOST
        ),
        metrics=ClusterMetrics(
            cpu_percent=0.5,
            memory_usage_bytes=1000,
            memory_limit_bytes=2000,
            volume_usage_bytes=None,
            sampled_at=None,
        ),
        unavailability_reason=None,
        sampled_at=None,
    )
    cluster = FakePostgresCluster(mode="compose", state=PostgresClusterState.HEALTHY, resource=crs)
    _patch_from_project(monkeypatch, cluster)

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        docker_provider=FakeDockerProvider(result=crs),
    )
    snap = monitor.snapshot()

    proj = snap.projects[0]
    assert proj.cluster is not None
    assert proj.cluster.mode == "compose"
    assert proj.cluster.owned is True
    assert proj.cluster.state is PostgresClusterState.HEALTHY
    assert proj.cluster.container is not None
    assert proj.cluster.container.pid == 42
    assert proj.cluster.metrics is not None
    assert proj.cluster.metrics.cpu_percent == 0.5


def test_external_cluster_null_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    snap = monitor.snapshot()

    proj = snap.projects[0]
    assert proj.cluster is not None
    assert proj.cluster.mode == "external"
    assert proj.cluster.owned is False
    assert proj.cluster.container is None
    assert proj.cluster.unavailability_reason == "external_not_owned"


def test_stopped_cluster_unavailability_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    crs = ClusterResourceSnapshot(
        container=None, metrics=None, unavailability_reason="stopped", sampled_at=None
    )
    cluster = FakePostgresCluster(mode="compose", state=PostgresClusterState.STOPPED, resource=crs)
    _patch_from_project(monkeypatch, cluster)

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        docker_provider=FakeDockerProvider(result=crs),
    )
    snap = monitor.snapshot()

    proj = snap.projects[0]
    assert proj.cluster is not None
    assert proj.cluster.state is PostgresClusterState.STOPPED
    assert proj.cluster.unavailability_reason == "stopped"


def test_missing_cluster_manifest_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.exceptions import ProjectManifestNotFoundError

    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    _patch_from_project(monkeypatch, raise_ex=ProjectManifestNotFoundError)

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    snap = monitor.snapshot()

    proj = snap.projects[0]
    assert proj.cluster is None


def test_docker_stats_error_carried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    crs = ClusterResourceSnapshot(
        container=None, metrics=None, unavailability_reason="stats_failed", sampled_at=None
    )
    cluster = FakePostgresCluster(mode="compose", state=PostgresClusterState.HEALTHY, resource=crs)
    _patch_from_project(monkeypatch, cluster)

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        docker_provider=FakeDockerProvider(result=crs),
    )
    snap = monitor.snapshot()

    proj = snap.projects[0]
    assert proj.cluster is not None
    assert proj.cluster.unavailability_reason == "stats_failed"


def test_git_divergence_carried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    git = GitActivity(
        default_branch="main",
        head_sha="abcdef1234567890",
        short_sha="abcdef1",
        branch="feature",
        ahead=2,
        behind=1,
        diff=None,
        state=GitActivityState.DIVERGED,
    )
    provider = FakeGitProvider(result=git)
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3", git_provider=provider)
    snap = monitor.snapshot()

    env = snap.environments[0]
    assert env.git.state is GitActivityState.DIVERGED
    assert env.git.ahead == 2
    assert env.git.behind == 1
    assert env.short_sha == "abcdef1"


def test_component_failure_isolation_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    e2 = str(uuid.uuid4())
    wt1 = tmp_path / "wt1"
    wt1.mkdir()
    wt2 = tmp_path / "wt2"
    wt2.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt1), branch="main"))
    _seed_env(catalog, _make_env(e2, worktree_path=str(wt2), branch="dev"))
    catalog.close()

    provider = FakeGitProvider(raise_ex=True)
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3", git_provider=provider)
    snap = monitor.snapshot()

    assert len(snap.environments) == 2
    for env in snap.environments:
        assert env.git.state is GitActivityState.ORPHAN
        assert env.git.head_sha is None


def test_catalog_error_raises_monitor_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from odoo_instance_sdk.exceptions import BackupCatalogError
    from odoo_instance_sdk.storage import backup_catalog as bc_mod

    def _boom(self: BackupCatalog) -> list[tuple[sqlite3.Row, sqlite3.Row | None]]:
        raise BackupCatalogError("sqlite boom")

    monkeypatch.setattr(bc_mod.BackupCatalog, "list_environments_with_runtimes", _boom)

    catalog = _make_catalog(tmp_path)
    catalog.close()
    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    with pytest.raises(MonitorError):
        monitor.snapshot()


def test_catalog_atomic_read_error_aborts_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an absent runtime means stopped; catalog failure is not lifecycle state."""
    from odoo_instance_sdk.exceptions import BackupCatalogError

    catalog = _make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _seed_env(catalog, _make_env(env_id, worktree_path=str(worktree)))
    catalog.close()

    def boom(self: BackupCatalog) -> list[tuple[sqlite3.Row, sqlite3.Row | None]]:
        raise BackupCatalogError("sqlite unavailable")

    monkeypatch.setattr(BackupCatalog, "list_environments_with_runtimes", boom)
    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", process_provider=FakeProcessProvider()
    )
    with pytest.raises(MonitorError, match="monitor catalog unavailable"):
        monitor.snapshot()


@pytest.mark.parametrize("payload", ["not-json", "[]"])
def test_malformed_health_response_is_not_ready_and_keeps_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    catalog = _make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _seed_env(catalog, _make_env(env_id, worktree_path=str(worktree)))
    _seed_runtime(catalog, env_id)
    catalog.close()
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    response = httpx.Response(200, text=payload)
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.httpx.get", lambda *args, **kwargs: response
    )
    provider = FakeProcessProvider(
        result=ProcessTreeResult(child_pids=(42,), process_count=2, cpu_percent=3.5, rss_bytes=99)
    )
    runtime = (
        EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3", process_provider=provider)
        .snapshot()
        .environments[0]
        .runtime
    )
    assert runtime.state is RuntimeState.NOT_READY
    assert (runtime.root_pid, runtime.process_count, runtime.cpu_percent, runtime.rss_bytes) == (
        12345,
        2,
        3.5,
        99,
    )


def test_runtime_is_read_once_in_atomic_catalog_snapshot_and_cpu_identity_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _seed_env(catalog, _make_env(env_id, worktree_path=str(worktree)))
    _seed_runtime(catalog, env_id, root_pid=111, create_time=1.0)
    catalog.close()
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    provider = FakeProcessProvider(
        result=ProcessTreeResult(child_pids=(), process_count=1, cpu_percent=None, rss_bytes=1)
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.EnvironmentMonitor._probe_readiness",
        lambda self, url: RuntimeState.READY,
    )
    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", process_provider=provider
    )
    snapshot = monitor.snapshot()
    assert snapshot.environments[0].runtime.root_pid == 111
    assert set(monitor._cpu_points) == {(111, 1.0)}


def test_psutil_missing_raises_extras_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    _seed_runtime(catalog, e1)
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    def _raise_import(*args: object, **kwargs: object) -> tuple[ProcessTreeResult, CpuPoint] | None:
        raise MonitorExtrasMissingError(
            "psutil is not installed; pip install odoo-instance-sdk[metrics]"
        )

    monkeypatch.setattr(
        "odoo_instance_sdk.internal.process_metrics.collect_process_tree", _raise_import
    )

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    with pytest.raises(MonitorExtrasMissingError) as exc_info:
        monitor.snapshot()
    assert "pip install odoo-instance-sdk[metrics]" in str(exc_info.value)


def test_redaction_no_secrets_or_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    cfg = tmp_path / "odoo.conf"
    _write_odoo_conf(cfg, http_port=8069)
    _seed_env(
        catalog,
        _make_env(
            e1,
            worktree_path=str(wt),
            generated_config_path=str(cfg),
            repository_root=str(tmp_path / "repo"),
            git_common_dir=str(tmp_path / "repo" / ".git"),
        ),
    )
    catalog.close()

    git = GitActivity(
        default_branch="main",
        head_sha="abcdef1234567890",
        short_sha="abcdef1",
        branch="main",
        ahead=0,
        behind=0,
        diff=None,
        state=GitActivityState.CLEAN,
    )
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        git_provider=FakeGitProvider(result=git),
    )
    snap = monitor.snapshot()

    payload = msgspec.json.encode(snap)
    text = payload.decode("utf-8")
    # No absolute paths from the catalog row leak into the snapshot.
    assert str(tmp_path) not in text
    assert "/wt" not in text
    assert "/venv" not in text
    assert "password" not in text.lower()
    assert "POSTGRES_PASSWORD" not in text
    assert "cmdline" not in text


def test_project_filter_known(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    e2 = str(uuid.uuid4())
    _seed_env(catalog, _make_env(e1, git_common_dir="/repoA/.git", repository_root="/repoA"))
    _seed_env(catalog, _make_env(e2, git_common_dir="/repoB/.git", repository_root="/repoB"))
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    full = monitor.snapshot()
    target_pid = full.projects[0].id
    filtered = monitor.snapshot(project_id=target_pid)

    assert len(filtered.projects) == 1
    assert filtered.projects[0].id == target_pid
    assert all(env.project_id == target_pid for env in filtered.environments)


def test_project_filter_unknown_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    snap = monitor.snapshot(project_id="project_unknown")

    assert snap.projects == ()
    assert snap.environments == ()


def test_watch_interval_floor_raises_value_error(tmp_path: Path) -> None:
    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")

    async def _drive() -> None:
        gen = monitor.watch(interval=0.05)
        async for _ in gen:  # pragma: no cover
            break

    with pytest.raises(ValueError):
        asyncio.run(_drive())


def test_watch_yields_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")

    async def _take(n: int) -> list[Snapshot]:
        results: list[Snapshot] = []
        async for snap in monitor.watch(interval=0.1):
            results.append(snap)
            if len(results) >= n:
                break
        return results

    snaps = asyncio.run(_take(2))
    assert len(snaps) == 2
    assert all(isinstance(s, Snapshot) for s in snaps)


def test_watch_cancellation_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")

    async def _cancel_after_one() -> None:
        gen = cast("AsyncGenerator[Snapshot, None]", monitor.watch(interval=0.1))
        await gen.__anext__()
        await gen.aclose()

    asyncio.run(_cancel_after_one())


def test_cpu_not_cached_between_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    _seed_runtime(catalog, e1)
    catalog.close()

    result = ProcessTreeResult(child_pids=(), process_count=1, cpu_percent=0.0, rss_bytes=1024)
    provider = FakeProcessProvider(result=result)
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json={"status": "pass"}))

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", process_provider=provider
    )
    monitor.snapshot()
    monitor.snapshot()

    assert provider.calls == 2


def test_git_cached_within_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    provider = FakeGitProvider()
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3", git_provider=provider)
    monitor.snapshot()
    monitor.snapshot()

    assert provider.calls == 1


def test_storage_cached_within_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "a.txt").write_bytes(b"hello")
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    s1 = monitor.snapshot().environments[0].storage
    s2 = monitor.snapshot().environments[0].storage

    assert s1 is s2


def test_fresh_instance_empties_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    provider_a = FakeGitProvider()
    provider_b = FakeGitProvider()
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor_a = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", git_provider=provider_a
    )
    monitor_a.snapshot()
    assert provider_a.calls == 1

    monitor_b = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", git_provider=provider_b
    )
    monitor_b.snapshot()
    assert provider_b.calls == 1


def test_default_monitor_requires_metrics_extra_even_for_empty_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    _make_catalog(tmp_path).close()
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "psutil":
            raise ImportError("psutil missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(MonitorExtrasMissingError, match=r"\[metrics\]"):
        EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot()


def test_cluster_status_cached_5s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt)))
    catalog.close()

    cluster = FakePostgresCluster(
        mode="compose",
        state=PostgresClusterState.HEALTHY,
        resource=ClusterResourceSnapshot(
            container=None, metrics=None, unavailability_reason="stopped", sampled_at=None
        ),
    )
    _patch_from_project(monkeypatch, cluster)

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        docker_provider=FakeDockerProvider(),
    )
    monitor.snapshot()
    monitor.snapshot()

    assert cluster._calls == 1
