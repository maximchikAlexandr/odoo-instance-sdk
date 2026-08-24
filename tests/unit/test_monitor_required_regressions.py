"""Regression coverage for the Review 10 monitor contracts."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from odoo_instance_sdk.exceptions import BackupCatalogError, MonitorError, MonitorExtrasMissingError
from odoo_instance_sdk.internal.process_metrics import CpuPoint, ProcessTreeResult
from odoo_instance_sdk.models import RuntimeState
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog
from tests.unit.monitor_support import (
    FakePostgresCluster,
    FakeProcessProvider,
    make_catalog,
    make_env,
    patch_from_project,
    seed_env,
    seed_runtime,
)


@pytest.fixture(autouse=True)
def _inject_process_provider_for_core_monitor_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """These regressions isolate monitor behavior from the metrics extra."""
    original_init = EnvironmentMonitor.__init__

    def init(self: EnvironmentMonitor, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("process_provider", FakeProcessProvider())
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EnvironmentMonitor, "__init__", init)


def test_projects_sort_by_id_even_when_paths_sort_inversely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project display paths are deliberately opposite to their stable IDs."""
    catalog = make_catalog(tmp_path)
    for name, key in (("z-path", "aaa"), ("a-path", "zzz")):
        root = tmp_path / name
        root.mkdir()
        worktree = root / "wt"
        worktree.mkdir()
        seed_env(
            catalog,
            make_env(
                str(uuid.uuid4()),
                repository_root=str(root),
                git_common_dir=str(root / ".git"),
                worktree_path=str(worktree),
            ),
        )
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.repo_key",
        lambda root, _common: "aaa" if Path(root).name == "z-path" else "zzz",
    )

    snapshot = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot()
    assert [project.id for project in snapshot.projects] == ["project_aaa", "project_zzz"]
    assert [project.name for project in snapshot.projects] == ["z-path", "a-path"]


def test_process_failure_is_local_but_missing_metrics_extra_is_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    for env_id, pid in ((first, 11), (second, 22)):
        worktree = tmp_path / env_id
        worktree.mkdir()
        seed_env(
            catalog,
            make_env(
                env_id,
                worktree_path=str(worktree),
                repository_root=str(worktree),
                git_common_dir=str(worktree / ".git"),
            ),
        )
        seed_runtime(catalog, env_id, root_pid=pid, create_time=float(pid))
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    class Provider:
        def collect(
            self, pid: int, create_time: float, *, prev_cpu_point: CpuPoint | None
        ) -> tuple[ProcessTreeResult, CpuPoint] | None:
            if pid == 11:
                raise RuntimeError("one process disappeared")
            return (
                ProcessTreeResult(child_pids=(), process_count=1, cpu_percent=2.0, rss_bytes=3),
                CpuPoint(0.0, 0.0),
            )

    monkeypatch.setattr(EnvironmentMonitor, "_probe_readiness", lambda *_: RuntimeState.READY)
    snapshot = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", process_provider=Provider()
    ).snapshot()
    runtimes = {env.runtime.root_pid: env.runtime for env in snapshot.environments}
    assert runtimes[None].state is RuntimeState.STOPPED
    assert runtimes[22].state is RuntimeState.READY

    class ExtrasProvider:
        def collect(
            self, root_pid: int, create_time: float, *, prev_cpu_point: CpuPoint | None
        ) -> tuple[ProcessTreeResult, CpuPoint] | None:
            raise MonitorExtrasMissingError("metrics missing")

    with pytest.raises(MonitorExtrasMissingError):
        EnvironmentMonitor(
            catalog_path=tmp_path / "catalog.sqlite3", process_provider=ExtrasProvider()
        ).snapshot()


def test_catalog_paths_are_redacted_from_public_monitor_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    catalog.close()

    def boom(self: BackupCatalog, **_: object) -> list[sqlite3.Row]:
        raise BackupCatalogError("cannot open /secret/catalog.sqlite3")

    monkeypatch.setattr(BackupCatalog, "list_environments_with_runtimes", boom)
    with pytest.raises(MonitorError) as caught:
        EnvironmentMonitor(
            catalog_path=tmp_path / "catalog.sqlite3", process_provider=FakeProcessProvider()
        ).snapshot()
    assert str(caught.value) == "monitor catalog unavailable"
    assert "/secret" not in str(caught.value)


@pytest.mark.dashboard
def test_catalog_path_is_redacted_from_api_error() -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.internal.serve import create_app

    class FailingMonitor:
        def snapshot(self, project_id: str | None = None) -> object:
            raise MonitorError("cannot open /secret/catalog.sqlite3")

    response = TestClient(create_app(headless=True, monitor=FailingMonitor())).get(
        "/api/v1/snapshot", headers={"host": "127.0.0.1"}
    )
    assert response.status_code == 500
    assert response.json() == {"error": "monitor snapshot failed"}
    assert "/secret" not in response.text


def test_cpu_identity_changes_prune_old_point_and_read_runtime_once_per_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    seed_env(catalog, make_env(env_id, worktree_path=str(worktree)))
    seed_runtime(catalog, env_id, root_pid=10, create_time=1.0)
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    class Provider:
        def collect(
            self, _: int, __: float, *, prev_cpu_point: CpuPoint | None
        ) -> tuple[ProcessTreeResult, CpuPoint] | None:
            return (
                ProcessTreeResult(child_pids=(), process_count=1, cpu_percent=0.0, rss_bytes=1),
                CpuPoint(0.0, 0.0),
            )

    monkeypatch.setattr(EnvironmentMonitor, "_probe_readiness", lambda *_: RuntimeState.READY)
    monitor = EnvironmentMonitor(
        catalog_path=tmp_path / "catalog.sqlite3", process_provider=Provider()
    )
    monitor.snapshot()
    assert set(monitor._cpu_points) == {(10, 1.0)}
    catalog = BackupCatalog(db_path=tmp_path / "catalog.sqlite3")
    catalog.upsert_environment_runtime(
        env_id,
        root_pid=20,
        create_time=2.0,
        started_at="2026-01-01T00:00:00",
        checkout_branch="main",
        commit_sha="a",
        http_url="http://127.0.0.1:1",
        http_port=1,
        database_name="db",
    )
    catalog.close()
    monitor.snapshot()
    assert set(monitor._cpu_points) == {(20, 2.0)}
