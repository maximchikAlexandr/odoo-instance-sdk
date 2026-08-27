from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest

from odoo_instance_sdk.internal.postgres_compose import SubprocessComposeRunner
from odoo_instance_sdk.models import GitActivity
from odoo_instance_sdk.resources.environment import EnvironmentState
from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.resources.postgres import PostgresCluster
from tests.unit.monitor_support import (
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
    write_odoo_conf as _write_odoo_conf,
)


@pytest.fixture(autouse=True)
def _inject_process_provider_for_core_monitor_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker/Git contracts inject process collection to stay narrowly focused."""
    original_init = EnvironmentMonitor.__init__

    def init(self: EnvironmentMonitor, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("process_provider", FakeProcessProvider())
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EnvironmentMonitor, "__init__", init)


def test_production_docker_collection_batches_two_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The monitor makes one inspect and one stats call for all uncached IDs."""
    catalog = _make_catalog(tmp_path)
    environment_ids: dict[str, str] = {}
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        wt = root / "wt"
        wt.mkdir()
        environment_ids[name] = str(uuid.uuid4())
        _seed_env(
            catalog,
            _make_env(
                environment_ids[name],
                repository_root=str(root),
                git_common_dir=str(root / ".git"),
                worktree_path=str(wt),
            ),
        )
    catalog.close()

    class Runner:
        requires_docker = True

        def __init__(self) -> None:
            self.inspect_calls = 0
            self.stats_calls = 0
            self.ids = {"a": "a" * 64, "b": "b" * 64}
            self.drop_inspect: set[str] = set()
            self.drop_stats: set[str] = set()

        def run(self, args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
            argv = list(args)
            if "ps" in argv:
                project = argv[argv.index("--project-name") + 1]
                ident = self.ids["a" if project.endswith("a") else "b"]
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps({"Service": "postgres", "ID": ident}), ""
                )
            ids = argv[argv.index("json") + 1 :]
            if "inspect" in argv:
                self.inspect_calls += 1
                out = [
                    {
                        "Id": cid,
                        "Name": f"/{cid[:12]}",
                        "Config": {"Image": "postgres:16"},
                        "State": {"Pid": 1},
                    }
                    for cid in ids
                    if cid not in self.drop_inspect
                ]
                return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")
            self.stats_calls += 1
            return subprocess.CompletedProcess(
                argv,
                0,
                "\n".join(
                    json.dumps({"container": cid, "CPUPerc": "1%", "MemUsage": "1MiB / 2MiB"})
                    for cid in ids
                    if cid not in self.drop_stats
                ),
                "",
            )

    runner = Runner()
    clusters = {
        str(tmp_path / "a"): FakePostgresCluster(project_id="a"),
        str(tmp_path / "b"): FakePostgresCluster(project_id="b"),
    }

    def subprocess_run(
        self: SubprocessComposeRunner, args: Sequence[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return runner.run(args, **kwargs)

    monkeypatch.setattr(SubprocessComposeRunner, "run", subprocess_run)
    # Real projects construct distinct default runners.  The monitor must use
    # its own shared runner for one inspect/stats batch, not either instance.
    for cluster in clusters.values():
        cluster._compose_runner = SubprocessComposeRunner()
    assert len({id(cluster.compose_runner) for cluster in clusters.values()}) == 2
    monkeypatch.setattr(
        PostgresCluster,
        "from_project",
        staticmethod(lambda path, **_: clusters[str(path)]),
    )
    monkeypatch.setattr("odoo_instance_sdk.resources.monitor.docker_available", lambda: True)

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    snapshot = monitor.snapshot()
    assert len(snapshot.projects) == 2
    assert runner.inspect_calls == 1
    assert runner.stats_calls == 1
    monitor.snapshot()
    assert runner.inspect_calls == 1
    assert runner.stats_calls == 1
    runner.ids["a"] = "c" * 64
    monitor.snapshot()
    assert runner.inspect_calls == 2
    assert runner.stats_calls == 2
    assert set(monitor._cluster_resource_cache) == {"b" * 64, "c" * 64}
    monitor._cluster_resource_cache.clear()
    runner.drop_inspect = {"b" * 64}
    partial = monitor.snapshot()
    assert {
        project.cluster.unavailability_reason
        for project in partial.projects
        if project.cluster is not None
    } == {None, "inspect_failed"}
    # A failed sample is not a TTL result: the next poll must retry it.
    assert "b" * 64 not in monitor._cluster_resource_cache
    inspect_calls_before_retry = runner.inspect_calls
    runner.drop_inspect.clear()
    retried = monitor.snapshot()
    assert runner.inspect_calls == inspect_calls_before_retry + 1
    assert all(
        project.cluster is not None and project.cluster.unavailability_reason is None
        for project in retried.projects
    )
    # Stats failures are likewise a retry boundary, never a cached sample.
    monitor._cluster_resource_cache.clear()
    runner.drop_stats = {"c" * 64}
    failed_stats = monitor.snapshot()
    assert any(
        project.cluster is not None and project.cluster.unavailability_reason == "stats_failed"
        for project in failed_stats.projects
    )
    calls_before_stats_retry = runner.stats_calls
    runner.drop_stats.clear()
    recovered_stats = monitor.snapshot()
    assert runner.stats_calls == calls_before_stats_retry + 1
    assert all(
        project.cluster is not None and project.cluster.unavailability_reason is None
        for project in recovered_stats.projects
    )
    catalog = _make_catalog(tmp_path)
    catalog.update_environment_state(environment_ids["b"], "removed", removed_at="2026-01-01")
    catalog.close()
    monitor.snapshot(project_id=partial.projects[0].id)
    assert set(monitor._cluster_resource_cache) == {"c" * 64}


def test_custom_runners_with_same_container_id_are_isolated_and_uncached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected runner output never crosses its explicit process boundary."""
    catalog = _make_catalog(tmp_path)
    clusters: dict[str, FakePostgresCluster] = {}

    class Runner:
        requires_docker = False

        def __init__(self, cpu: str) -> None:
            self.cpu = cpu
            self.inspect_calls = 0
            self.stats_calls = 0

        def run(self, args: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
            argv = list(args)
            container_id = "same" * 16
            if "ps" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps({"Service": "postgres", "ID": container_id}), ""
                )
            if "inspect" in argv:
                self.inspect_calls += 1
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(
                        [
                            {
                                "Id": container_id,
                                "Name": "/postgres",
                                "Config": {"Image": "pg"},
                                "State": {"Pid": 1},
                            }
                        ]
                    ),
                    "",
                )
            self.stats_calls += 1
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {"container": container_id, "CPUPerc": self.cpu, "MemUsage": "1MiB / 2MiB"}
                ),
                "",
            )

    runners = [Runner("1%"), Runner("2%")]
    for name, runner in zip(("a", "b"), runners, strict=True):
        root = tmp_path / name
        root.mkdir()
        worktree = root / "wt"
        worktree.mkdir()
        _seed_env(
            catalog,
            _make_env(
                str(uuid.uuid4()),
                repository_root=str(root),
                git_common_dir=str(root / ".git"),
                worktree_path=str(worktree),
            ),
        )
        cluster = FakePostgresCluster(project_id=name)
        cluster._compose_runner = runner
        clusters[str(root)] = cluster
    catalog.close()
    monkeypatch.setattr(
        PostgresCluster, "from_project", staticmethod(lambda path, **_: clusters[str(path)])
    )

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    first = monitor.snapshot()
    second = monitor.snapshot()
    first_metrics = [project.cluster.metrics for project in first.projects if project.cluster]
    second_metrics = [project.cluster.metrics for project in second.projects if project.cluster]
    assert all(metrics is not None for metrics in first_metrics + second_metrics)
    assert [metrics.cpu_percent for metrics in first_metrics if metrics is not None] == [1.0, 2.0]
    assert [metrics.cpu_percent for metrics in second_metrics if metrics is not None] == [1.0, 2.0]
    assert [(runner.inspect_calls, runner.stats_calls) for runner in runners] == [(2, 2), (2, 2)]
    assert monitor._cluster_resource_cache == {}


def test_production_git_cache_reuses_and_invalidates_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default collection is reused only while both cheap Git identities match."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    identities = iter(
        [
            ("head-a", "head-a", "topic", "tip-a"),
            ("head-a", "head-a", "topic", "tip-a"),
            ("head-b", "head-b", "topic", "tip-a"),
            ("head-b", "head-b", "topic", "tip-b"),
        ]
    )
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor._resolve_identity", lambda _: next(identities)
    )

    def collect(_: Path, identity: tuple[str, str, str, str | None]) -> GitActivity:
        calls.append((identity[0], identity[3]))
        return FakeGitProvider().collect(worktree)

    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.collect_git_activity_from_identity", collect
    )
    monitor = EnvironmentMonitor()
    for _ in range(4):
        monitor._collect_git(worktree)
    assert calls == [("head-a", "tip-a"), ("head-b", "tip-a"), ("head-b", "tip-b")]
    assert len(monitor._git_cache) == 1


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

    assert snap.schema_version == 2
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
