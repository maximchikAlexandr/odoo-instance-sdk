from __future__ import annotations

import asyncio
import sqlite3
import uuid
from pathlib import Path

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
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

# --------------------------------------------------------------------- helpers


def _make_env(
    env_id: str,
    *,
    name: str = "test",
    repository_root: str = "/repo",
    git_common_dir: str = "/repo/.git",
    branch: str = "main",
    worktree_path: str = "/wt",
    generated_config_path: str = "/wt/odoo.conf",
    python_environment_path: str = "/venv",
    python_environment_owned: bool = False,
    dependency_lock_path: str = "/lock",
    db_mode: str = "shared",
    source_db_name: str | None = "mydb",
    target_db_name: str | None = None,
    state: str = "ready",
) -> dict[str, object]:
    return {
        "id": env_id,
        "name": name,
        "repository_root": repository_root,
        "git_common_dir": git_common_dir,
        "branch": branch,
        "base_ref": "HEAD",
        "worktree_path": worktree_path,
        "generated_config_path": generated_config_path,
        "python_environment_path": python_environment_path,
        "python_environment_owned": python_environment_owned,
        "dependency_lock_path": dependency_lock_path,
        "db_mode": db_mode,
        "source_db_name": source_db_name,
        "target_db_name": target_db_name,
        "backup_id": None,
        "runtime_json": "{}",
        "state": state,
        "created_at": "2026-01-01T00:00:00",
        "last_used_at": None,
        "removed_at": None,
        "last_error": None,
    }


def _runtime_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "root_pid": 12345,
        "create_time": 1700000000.0,
        "started_at": "2026-01-01T00:00:00",
        "checkout_branch": "main",
        "commit_sha": "abc123def456",
        "http_url": "http://127.0.0.1:8069",
        "http_port": 8069,
        "database_name": "mydb",
    }
    base.update(overrides)
    return base


def _make_catalog(tmp_path: Path) -> BackupCatalog:
    return BackupCatalog(db_path=tmp_path / "catalog.sqlite3")


def _seed_env(catalog: BackupCatalog, env: dict[str, object]) -> None:
    catalog.create_environment(env)


def _seed_runtime(catalog: BackupCatalog, env_id: str, **kwargs: object) -> None:
    catalog.upsert_environment_runtime(env_id, **_runtime_kwargs(**kwargs))  # type: ignore[arg-type]


class FakeProcessProvider:
    def __init__(
        self,
        *,
        result: ProcessTreeResult | None = None,
        raise_ex: type[BaseException] | None = None,
    ) -> None:
        self.calls = 0
        self._result = result
        self._raise = raise_ex

    def collect(
        self, root_pid: int, create_time: float, *, prev_cpu_point: CpuPoint | None
    ) -> tuple[ProcessTreeResult, CpuPoint] | None:
        self.calls += 1
        if self._raise is not None:
            raise self._raise("psutil missing")
        if self._result is None:
            return None
        return self._result, CpuPoint(times_cpu=0.0, timestamp=0.0)


class FakeGitProvider:
    def __init__(self, *, result: GitActivity | None = None, raise_ex: bool = False) -> None:
        self.calls = 0
        self._result = result or GitActivity(
            default_branch="main",
            head_sha="abcdef1234567890",
            short_sha="abcdef1",
            branch="main",
            ahead=0,
            behind=0,
            diff=None,
            state=GitActivityState.CLEAN,
        )
        self._raise = raise_ex

    def collect(self, worktree: Path) -> GitActivity:
        self.calls += 1
        if self._raise:
            raise RuntimeError("git failed")
        return self._result


class FakeDockerProvider:
    def __init__(self, *, result: ClusterResourceSnapshot | None = None) -> None:
        self.calls = 0
        self._result = result

    def collect(
        self,
        *,
        compose_file: Path,
        compose_project_name: str,
        service: str,
        state: PostgresClusterState,
    ) -> ClusterResourceSnapshot:
        self.calls += 1
        if self._result is not None:
            return self._result
        return ClusterResourceSnapshot(
            container=None,
            metrics=None,
            unavailability_reason="stats_failed",
            sampled_at=None,
        )


class FakePostgresCluster:
    """Minimal stand-in returned by monkeypatched PostgresCluster.from_project."""

    def __init__(
        self,
        *,
        mode: str = "compose",
        endpoint_host: str = "127.0.0.1",
        endpoint_port: int = 5432,
        state: PostgresClusterState = PostgresClusterState.HEALTHY,
        resource: ClusterResourceSnapshot | None = None,
        project_id: str = "fake_key",
    ) -> None:
        self._mode = mode
        self._endpoint_host = endpoint_host
        self._endpoint_port = endpoint_port
        self._state = state
        self._resource = resource
        self._project_id = project_id
        self._compose_runner = None  # type: ignore[assignment]
        self._calls = 0

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def owned(self) -> bool:
        return self._mode == "compose"

    @property
    def endpoint_host(self) -> str:
        return self._endpoint_host

    @property
    def endpoint_port(self) -> int:
        return self._endpoint_port

    @property
    def compose_project_name(self) -> str:
        return f"odcli_pg_{self._project_id}"

    @property
    def compose_file(self) -> Path:
        return Path("/fake/compose.yaml")

    def status(self) -> PostgresClusterState:
        self._calls += 1
        return self._state

    def resource_snapshot(self) -> ClusterResourceSnapshot | None:
        if self._mode == "external":
            return None
        return self._resource


def _patch_from_project(
    monkeypatch: pytest.MonkeyPatch,
    cluster: FakePostgresCluster | None = None,
    *,
    raise_ex: type[BaseException] | None = None,
) -> None:
    def _from_project(project_path: str | Path, **kwargs: object) -> PostgresCluster:
        if raise_ex is not None:
            raise raise_ex(str(project_path))
        return cluster  # type: ignore[return-value]

    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(_from_project))


def _write_odoo_conf(path: Path, *, http_port: int = 8069) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[options]\n"
        f"http_port = {http_port}\n"
        "http_interface = 127.0.0.1\n"
        "data_dir = /tmp/odoo_data\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------- tests


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

    def _boom(self: BackupCatalog, **kwargs: object) -> list[sqlite3.Row]:
        raise BackupCatalogError("sqlite boom")

    monkeypatch.setattr(bc_mod.BackupCatalog, "list_environments", _boom)

    catalog = _make_catalog(tmp_path)
    catalog.close()
    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    with pytest.raises(MonitorError):
        monitor.snapshot()


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
        gen = monitor.watch(interval=0.1)
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


def test_snapshot_schema_version_and_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _make_catalog(tmp_path)
    e_b = "zzzzzzzz-0000-0000-0000-000000000000"
    e_a = "aaaaaaaa-0000-0000-0000-000000000000"
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e_b, worktree_path=str(wt), branch="main"))
    _seed_env(
        catalog,
        _make_env(
            e_a,
            worktree_path=str(wt),
            branch="dev",
            git_common_dir="/repo2/.git",
            repository_root="/repo2",
        ),
    )
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    snap = monitor.snapshot()

    assert snap.schema_version == 1
    assert snap.generated_at.tzinfo is not None
    env_ids = [env.id for env in snap.environments]
    assert env_ids == sorted(env_ids)


def test_allocated_http_port_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    cfg = tmp_path / "odoo.conf"
    _write_odoo_conf(cfg, http_port=8123)
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt), generated_config_path=str(cfg)))
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    snap = monitor.snapshot()

    env = snap.environments[0]
    assert env.allocated_http_port == 8123


def test_lifecycle_state_from_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e1 = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(catalog, _make_env(e1, worktree_path=str(wt), state="creating"))
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    snap = monitor.snapshot()

    env = snap.environments[0]
    assert env.lifecycle_state is EnvironmentState.CREATING


def test_database_field_copy_vs_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path)
    e_copy = str(uuid.uuid4())
    e_shared = str(uuid.uuid4())
    wt = tmp_path / "wt"
    wt.mkdir()
    _seed_env(
        catalog,
        _make_env(
            e_copy,
            worktree_path=str(wt),
            db_mode="copy",
            source_db_name="src",
            target_db_name="tgt",
            branch="main",
        ),
    )
    _seed_env(
        catalog,
        _make_env(
            e_shared,
            worktree_path=str(wt),
            db_mode="shared",
            source_db_name="srcdb",
            target_db_name=None,
            branch="dev",
            git_common_dir="/repo2/.git",
            repository_root="/repo2",
        ),
    )
    catalog.close()

    _patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    snap = monitor.snapshot()

    by_id = {env.id: env for env in snap.environments}
    assert by_id[e_copy].database == "tgt"
    assert by_id[e_shared].database == "srcdb"
