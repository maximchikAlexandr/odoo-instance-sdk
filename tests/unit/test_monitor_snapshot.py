from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import msgspec
import pytest

from odoo_instance_sdk.exceptions import MonitorError
from odoo_instance_sdk.execution import Command
from odoo_instance_sdk.internal.proc import (
    PreparedStep,
    ProcessResult,
    RecordingExecutor,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.process_metrics import ProcessTreeResult
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import (
    ClusterContainer,
    ClusterMetrics,
    ClusterResourceSnapshot,
    GitActivity,
    GitActivityState,
    GitDiff,
    PidScope,
    PostgresClusterState,
    RuntimeState,
    Snapshot,
)
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.resources.postgres import PostgresCluster
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
    runtime_kwargs as _runtime_kwargs,
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


def test_registered_project_without_environment_is_discovered(tmp_path: Path) -> None:
    catalog = _make_catalog(tmp_path)
    root = tmp_path / "project-only"
    common = root / ".git"
    common.mkdir(parents=True)
    project_id = f"project_{repo_key(root, common)}"
    catalog._register_project(project_id, root, common)
    catalog.close()

    monkeypatch = pytest.MonkeyPatch()
    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    try:
        snap = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot()
    finally:
        monkeypatch.undo()

    assert [project.id for project in snap.projects] == [project_id]
    assert snap.projects[0].environment_count == 0
    assert snap.environments == ()


def test_project_runtime_is_collected_with_shared_runtime_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    root = tmp_path / "project-only"
    common = root / ".git"
    common.mkdir(parents=True)
    project_id = f"project_{repo_key(root, common)}"
    catalog._register_project(project_id, root, common)
    catalog._upsert_runtime("project", project_id, **_runtime_kwargs(root_pid=4242))  # type: ignore[arg-type]
    catalog.close()

    provider = FakeProcessProvider(
        result=ProcessTreeResult(
            child_pids=(4243,), process_count=2, cpu_percent=1.25, rss_bytes=99
        )
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.EnvironmentMonitor._probe_readiness",
        lambda self, url: RuntimeState.READY,
    )
    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        process_provider=provider,
        git_provider=FakeGitProvider(),
        docker_provider=FakeDockerProvider(),
    )

    runtime = monitor.snapshot().projects[0].runtime

    assert runtime is not None
    assert runtime.state is RuntimeState.READY
    assert runtime.root_pid == 4242
    assert runtime.child_pids == (4243,)
    assert runtime.process_count == 2
    assert runtime.cpu_percent == 1.25
    assert runtime.rss_bytes == 99
    assert runtime.http_url == "http://127.0.0.1:8069"
    assert runtime.database_name == "mydb"
    assert provider.calls == 1


def test_project_runtime_absent_differs_from_stale_stopped_runtime(tmp_path: Path) -> None:
    catalog = _make_catalog(tmp_path)
    projects: list[str] = []
    for name in ("absent", "stale"):
        root = tmp_path / name
        common = root / ".git"
        common.mkdir(parents=True)
        project_id = f"project_{repo_key(root, common)}"
        projects.append(project_id)
        catalog._register_project(project_id, root, common)
        if name == "stale":
            catalog._upsert_runtime("project", project_id, **_runtime_kwargs(root_pid=5252))  # type: ignore[arg-type]
    catalog.close()

    snapshot = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        git_provider=FakeGitProvider(),
        docker_provider=FakeDockerProvider(),
    ).snapshot()
    by_id = {project.id: project for project in snapshot.projects}

    assert by_id[projects[0]].runtime is None
    stopped = by_id[projects[1]].runtime
    assert stopped is not None
    assert stopped.state is RuntimeState.STOPPED
    assert stopped.root_pid is None
    assert stopped.child_pids == ()
    assert stopped.process_count == 0


def test_mixed_project_and_environment_runtime_ownership_is_combined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    project_id = f"project_{repo_key(Path('/repo'), Path('/repo/.git'))}"
    catalog._register_project(project_id, "/repo", "/repo/.git")
    _seed_env(catalog, _make_env(env_id))
    _seed_runtime(catalog, env_id, root_pid=1111)
    catalog._upsert_runtime("project", project_id, **_runtime_kwargs(root_pid=2222))  # type: ignore[arg-type]
    catalog.close()

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.EnvironmentMonitor._probe_readiness",
        lambda self, url: RuntimeState.READY,
    )
    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        process_provider=FakeProcessProvider(
            result=ProcessTreeResult(child_pids=(), process_count=1, cpu_percent=0.5, rss_bytes=8)
        ),
        git_provider=FakeGitProvider(),
        docker_provider=FakeDockerProvider(),
    )

    snapshot = monitor.snapshot()

    assert len(snapshot.projects) == 1
    assert snapshot.projects[0].id == project_id
    assert snapshot.projects[0].environment_count == 1
    assert snapshot.projects[0].runtime is not None
    assert snapshot.projects[0].runtime.root_pid == 2222
    assert len(snapshot.environments) == 1
    assert snapshot.environments[0].project_id == project_id
    assert snapshot.environments[0].runtime.root_pid == 1111


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

    def _boom(self: BackupCatalog, **_: object) -> object:
        raise BackupCatalogError("sqlite boom")

    monkeypatch.setattr(bc_mod.BackupCatalog, "_monitor_snapshot_rows", _boom)

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

    def boom(self: BackupCatalog, **_: object) -> object:
        raise BackupCatalogError("sqlite unavailable")

    monkeypatch.setattr(BackupCatalog, "_monitor_snapshot_rows", boom)
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


def test_project_filter_avoids_cluster_work_for_unknown_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    _seed_env(
        catalog, _make_env(str(uuid.uuid4()), repository_root="/repo", git_common_dir="/repo/.git")
    )
    catalog.close()
    calls: list[Path] = []

    def forbidden(repo_root: Path) -> FakePostgresCluster:
        calls.append(repo_root)
        raise AssertionError("filtered project must not load a cluster")

    monkeypatch.setattr(PostgresCluster, "from_project", forbidden)
    snap = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot(
        project_id="project_unknown"
    )
    assert snap.projects == ()
    assert snap.environments == ()
    assert calls == []


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


def test_snapshot_command_is_immutable_and_snapshot_delegates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = Snapshot(
        schema_version=3,
        generated_at=datetime(2020, 1, 1, tzinfo=UTC),
        projects=(),
        environments=(),
    )
    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    calls: list[tuple[str | None, bool]] = []

    def collect(*, project_id: str | None = None, include_removed: bool = False) -> Snapshot:
        calls.append((project_id, include_removed))
        return expected

    monkeypatch.setattr(
        EnvironmentMonitor,
        "_snapshot_impl",
        lambda _self, **kwargs: collect(**kwargs),
    )
    command = monitor.snapshot_command("project-1", include_removed=True)
    original_plan = command.plan

    assert command.run() == expected
    assert monitor.snapshot("project-1", include_removed=True) == expected
    assert command.plan == original_plan
    assert calls == [("project-1", True), ("project-1", True)]


def test_snapshot_command_records_each_catalog_process_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (tmp_path / "cache").mkdir()
    (tmp_path / "artifacts").mkdir()
    config = tmp_path / "odoo.conf"
    config.write_text("", encoding="utf-8")
    lock = tmp_path / "requirements.lock"
    lock.write_text("", encoding="utf-8")
    _seed_env(
        catalog,
        _make_env(
            env_id,
            worktree_path=str(worktree),
            generated_config_path=str(config),
            dependency_lock_path=str(lock),
        ),
    )
    catalog.close()

    executor = RecordingExecutor(
        result_factory=lambda step: ProcessResult(
            argv=step.argv,
            returncode=0,
            stdout=(
                "abcdef0123456789"
                if step.step_id.endswith("git.head")
                else "feature"
                if step.step_id.endswith("git.branch")
                else "fedcba9876543210"
                if step.step_id.endswith("git.upstream")
                else "fedcba9876543210"
                if step.step_id.endswith("git.local_main")
                else "base123"
                if step.step_id.endswith("git.upstream_merge_base")
                else "2"
                if step.step_id.endswith("git.upstream_ahead")
                else "1"
                if step.step_id.endswith("git.upstream_behind")
                else "3\t4\tfile.py\n"
                if step.step_id.endswith("git.upstream_diff")
                else "42\t/tmp/worktree"
                if step.step_id.endswith("storage.worktree")
                else "5\t/tmp/cache"
                if step.step_id.endswith("storage.cache")
                else "7\t/tmp/artifacts"
                if step.step_id.endswith("storage.artifacts")
                else "17"
                if step.step_id.endswith("postgres.identity")
                else "[]"
            ),
            stderr="",
            duration=0.0,
            cwd=cast("PreparedStep", step).cwd,
            environment=cast("PreparedStep", step).environment,
        )
    )
    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3", _executor=executor)

    command = monitor.snapshot_command()
    process_ids = tuple(step.step_id for step in command.plan.process_steps)
    assert process_ids[:3] == (
        f"monitor.{env_id}.git.head",
        f"monitor.{env_id}.storage.worktree",
        f"monitor.{env_id}.postgres.identity",
    )
    assert any(step_id.startswith("monitor.project_") for step_id in process_ids)
    snapshot = command.run()
    assert snapshot.environments[0].git.head_sha == "abcdef0123456789"
    assert snapshot.environments[0].git.branch == "feature"
    assert snapshot.environments[0].git.ahead == 2
    assert snapshot.environments[0].git.behind == 1
    assert snapshot.environments[0].git.diff == GitDiff(added=3, deleted=4)
    assert snapshot.environments[0].storage.worktree_bytes == 42
    assert snapshot.environments[0].storage.total_bytes == 54
    assert snapshot.environments[0].storage.other_files_bytes == 12
    assert tuple(step.step_id for step in executor.executed) == process_ids


def test_snapshot_command_failed_git_and_docker_probes_are_not_retried(
    tmp_path: Path,
) -> None:
    catalog = _make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _seed_env(catalog, _make_env(env_id, worktree_path=str(worktree)))
    catalog.close()

    executor = RecordingExecutor(
        result_factory=lambda step: ProcessResult(
            argv=step.argv,
            returncode=127 if ".git." in step.step_id or ".docker." in step.step_id else 0,
            stdout="",
            stderr="probe failed",
            duration=0.0,
            cwd=cast("PreparedStep", step).cwd,
            environment=cast("PreparedStep", step).environment,
        )
    )
    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3", _executor=executor)
    command = monitor.snapshot_command()
    process_ids = tuple(step.step_id for step in command.plan.process_steps)

    snapshot = command.run()

    assert snapshot.environments[0].git.state is GitActivityState.ORPHAN
    assert tuple(step.step_id for step in executor.executed) == process_ids


def test_hanging_storage_probe_is_bounded_and_keeps_sibling_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed-environment inventory remains useful if one probe hangs."""
    catalog = _make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _seed_env(
        catalog,
        _make_env(
            env_id,
            worktree_path=str(worktree),
            source_db_name=None,
        ),
    )
    catalog.close()

    hanging = tmp_path / "hanging-du"
    hanging.write_text("#!/bin/sh\nexec sleep 10\n", encoding="utf-8")
    hanging.chmod(0o755)
    real_which = shutil.which

    def which(name: str) -> str | None:
        return str(hanging) if name == "du" else real_which(name)

    monkeypatch.setattr("odoo_instance_sdk.resources.monitor.shutil.which", which)
    monkeypatch.setattr("odoo_instance_sdk.resources.monitor._PROBE_TIMEOUT_SECONDS", 0.05)

    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3",
        git_provider=FakeGitProvider(),
        docker_provider=FakeDockerProvider(),
        _executor=SubprocessExecutor(),
    )

    snapshot = monitor.snapshot()

    assert len(snapshot.environments) == 1
    assert snapshot.environments[0].git.branch == "main"
    assert snapshot.environments[0].storage.complete is False


def test_watch_builds_a_fresh_snapshot_command_per_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = Snapshot(
        schema_version=3,
        generated_at=datetime(2020, 1, 1, tzinfo=UTC),
        projects=(),
        environments=(),
    )
    monitor = EnvironmentMonitor()
    commands: list[Command[Snapshot]] = []

    original = EnvironmentMonitor.snapshot_command

    def fresh(
        self: EnvironmentMonitor,
        project_id: str | None = None,
        *,
        include_removed: bool = False,
    ) -> Command[Snapshot]:
        command = original(self, project_id, include_removed=include_removed)
        commands.append(command)
        return command

    monkeypatch.setattr(
        EnvironmentMonitor,
        "_snapshot_impl",
        lambda _self, **_kwargs: expected,
    )
    monkeypatch.setattr(EnvironmentMonitor, "snapshot_command", fresh)

    async def take_two() -> None:
        generator = cast("AsyncGenerator[Snapshot, None]", monitor.watch(interval=0.1))
        await generator.__anext__()
        await generator.__anext__()
        await generator.aclose()

    asyncio.run(take_two())
    assert len(commands) == 2
    assert commands[0] is not commands[1]
    assert commands[0].plan == commands[1].plan


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


def test_default_monitor_works_with_core_process_dependency(tmp_path: Path) -> None:
    _make_catalog(tmp_path).close()
    snapshot = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot()
    assert snapshot.environments == ()


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
