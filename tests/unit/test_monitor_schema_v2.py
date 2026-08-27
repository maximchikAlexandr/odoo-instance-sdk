from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import msgspec
import pytest

import odoo_instance_sdk as sdk
from odoo_instance_sdk.internal.address import AddressState
from odoo_instance_sdk.internal.git_worktree import WorktreeInfo
from odoo_instance_sdk.models import (
    EnvironmentArtifacts,
    EnvironmentState,
    PortObservation,
    RuntimeMetrics,
    RuntimeState,
)
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
    write_odoo_conf,
)


@pytest.fixture(autouse=True)
def _inject_process_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    original_init = EnvironmentMonitor.__init__

    def init(self: EnvironmentMonitor, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("process_provider", FakeProcessProvider())
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(EnvironmentMonitor, "__init__", init)


def test_schema_v2_types_are_public_frozen_and_exact() -> None:
    assert sdk.PortObservation is PortObservation
    assert sdk.EnvironmentArtifacts is EnvironmentArtifacts
    assert tuple(field.name for field in msgspec.structs.fields(EnvironmentArtifacts)) == (
        "worktree_exists",
        "worktree_registered",
        "config_exists",
        "python_exists",
        "python_contained",
        "dependency_lock_exists",
        "backup_exists",
    )
    artifacts = EnvironmentArtifacts(
        worktree_exists=False,
        worktree_registered=False,
        config_exists=False,
        python_exists=False,
        python_contained=True,
        dependency_lock_exists=False,
        backup_exists=None,
    )
    with pytest.raises(AttributeError):
        artifacts.worktree_exists = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        EnvironmentArtifacts(  # type: ignore[call-arg]
            worktree_exists=False,
            worktree_registered=False,
            config_exists=False,
            python_exists=False,
            python_contained=True,
            dependency_lock_exists=False,
            backup_exists=None,
            extra=False,
        )


def test_catalog_include_removed_is_keyword_only_and_atomic(tmp_path: Path) -> None:
    catalog = make_catalog(tmp_path)
    seed_env(catalog, make_env(str(uuid.uuid4()), state="removed"))
    assert catalog.list_environments_with_runtimes() == []
    rows = catalog.list_environments_with_runtimes(include_removed=True)
    assert len(rows) == 1
    assert rows[0][0]["state"] == "removed"
    with pytest.raises(TypeError):
        catalog.list_environments_with_runtimes(True)  # type: ignore[call-arg]
    catalog.close()


def test_removed_only_project_and_count_are_included_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    active_id = str(uuid.uuid4())
    removed_id = str(uuid.uuid4())
    seed_env(catalog, make_env(active_id, repository_root="/active", git_common_dir="/active/.git"))
    seed_env(
        catalog,
        make_env(
            removed_id,
            repository_root="/removed",
            git_common_dir="/removed/.git",
            state="removed",
        ),
    )
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    monitor = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
    active = monitor.snapshot()
    included = monitor.snapshot(include_removed=True)

    assert [env.id for env in active.environments] == [active_id]
    assert {env.id for env in included.environments} == {active_id, removed_id}
    removed_projects = [project for project in included.projects if project.name == "removed"]
    assert len(removed_projects) == 1
    assert removed_projects[0].environment_count == 1
    removed = next(env for env in included.environments if env.id == removed_id)
    assert removed.lifecycle_state is EnvironmentState.REMOVED
    assert removed.runtime.state is RuntimeState.STOPPED
    assert removed.observed_port is None


def test_monitor_reads_backup_metadata_before_catalog_close_and_reconciles_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    backup_id = str(uuid.uuid4())
    backup_path = tmp_path / "backup.zip"
    backup_path.write_bytes(b"backup")
    catalog.start_download(
        backup_id,
        "https://odoo.example",
        "db",
        "zip",
        False,
        backup_path,
    )
    catalog.success_download(backup_id, "backup.zip", 6, "sha256")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    config = worktree / "odoo.conf"
    write_odoo_conf(config, http_port=8123)
    python = worktree.parent / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("python")
    lock = worktree / "requirements.lock"
    lock.write_text("httpx")
    env_id = str(uuid.uuid4())
    seed_env(
        catalog,
        make_env(
            env_id,
            repository_root=str(tmp_path),
            git_common_dir=str(tmp_path / ".git"),
            worktree_path=str(worktree),
            generated_config_path=str(config),
            python_environment_path=str(python.parent.parent),
            python_environment_owned=True,
            dependency_lock_path=str(lock),
            backup_id=backup_id,
        ),
    )
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.worktree_list_porcelain",
        lambda _: [
            WorktreeInfo(
                worktree=str(worktree), head="abc", branch="main", locked=False, prunable=False
            )
        ],
    )

    env = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot().environments[0]

    assert env.artifacts == EnvironmentArtifacts(
        worktree_exists=True,
        worktree_registered=True,
        config_exists=True,
        python_exists=True,
        python_contained=True,
        dependency_lock_exists=True,
        backup_exists=True,
    )
    assert env.allocated_http_port == 8123


@pytest.mark.parametrize("backup_state", ["failed", "deleted", "missing-row", "missing-file"])
def test_backup_artifact_requires_available_row_and_recorded_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backup_state: str,
) -> None:
    catalog = make_catalog(tmp_path)
    backup_id = str(uuid.uuid4())
    backup_path = tmp_path / "backup.zip"
    backup_path.write_bytes(b"backup")
    catalog.start_download(backup_id, "https://odoo.example", "db", "zip", False, backup_path)
    if backup_state == "failed":
        catalog.fail_download(backup_id, "network", "download failed")
    else:
        catalog.success_download(backup_id, "backup.zip", 6, "sha256")
        if backup_state == "deleted":
            catalog.record_deletion(backup_id)
        elif backup_state == "missing-file":
            backup_path.unlink()
    env_id = str(uuid.uuid4())
    seed_env(catalog, make_env(env_id, backup_id=backup_id))
    if backup_state == "missing-row":
        catalog._conn.execute("PRAGMA foreign_keys=OFF")
        catalog._conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
        catalog._conn.execute("PRAGMA foreign_keys=ON")
        catalog._conn.commit()
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))

    env = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot().environments[0]

    assert env.artifacts.backup_exists is False


@pytest.mark.parametrize("address_state", [AddressState.FREE, AddressState.UNKNOWN])
def test_port_observation_maps_bounded_address_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    address_state: AddressState,
) -> None:
    catalog = make_catalog(tmp_path)
    config = tmp_path / "odoo.conf"
    write_odoo_conf(config, http_port=8125)
    env_id = str(uuid.uuid4())
    seed_env(catalog, make_env(env_id, generated_config_path=str(config)))
    seed_runtime(catalog, env_id)
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    monkeypatch.setattr(
        EnvironmentMonitor,
        "_collect_runtime",
        lambda *_: RuntimeMetrics(
            state=RuntimeState.READY,
            root_pid=1,
            child_pids=(),
            process_count=1,
            cpu_percent=0.0,
            rss_bytes=1,
            started_at=None,
            http_url="http://127.0.0.1:8069",
            http_port=8069,
            database_name="db",
            commit_sha="abc",
            branch="main",
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.probe_address", lambda *_: address_state
    )

    env = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot().environments[0]

    assert env.observed_port is PortObservation(address_state.value)


def test_monitor_planning_calls_atomic_catalog_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    seed_env(catalog, make_env(str(uuid.uuid4())))
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    original = BackupCatalog.list_environments_with_runtimes
    calls: list[bool] = []

    def list_once(
        self: BackupCatalog, *, include_removed: bool = False
    ) -> list[tuple[sqlite3.Row, sqlite3.Row | None]]:
        calls.append(include_removed)
        return original(self, include_removed=include_removed)

    monkeypatch.setattr(BackupCatalog, "list_environments_with_runtimes", list_once)
    EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot(include_removed=True)

    assert calls == [True]


def test_artifact_failures_are_isolated_and_port_observation_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    config = tmp_path / "odoo.conf"
    write_odoo_conf(config, http_port=8124)
    seed_env(
        catalog,
        make_env(
            env_id,
            generated_config_path=str(config),
            worktree_path=str(tmp_path / "missing-worktree"),
        ),
    )
    seed_runtime(catalog, env_id)
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    monkeypatch.setattr(EnvironmentMonitor, "_probe_readiness", lambda *_: RuntimeState.READY)
    monkeypatch.setattr(
        EnvironmentMonitor,
        "_collect_runtime",
        lambda *_: RuntimeMetrics(
            state=RuntimeState.READY,
            root_pid=12345,
            child_pids=(),
            process_count=1,
            cpu_percent=0.0,
            rss_bytes=1,
            started_at=None,
            http_url="http://127.0.0.1:8069",
            http_port=8069,
            database_name="mydb",
            commit_sha="abc123",
            branch="main",
        ),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.probe_address",
        lambda *_: AddressState.OCCUPIED,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.worktree_list_porcelain",
        lambda _: (_ for _ in ()).throw(OSError("git unavailable")),
    )

    env = EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3").snapshot().environments[0]

    assert env.runtime.state is RuntimeState.READY
    assert env.observed_port is PortObservation.OCCUPIED
    assert env.artifacts.worktree_registered is False
    assert env.artifacts.worktree_exists is False


def test_removed_runtime_never_calls_health_or_port_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    env_id = str(uuid.uuid4())
    seed_env(catalog, make_env(env_id, state="removed"))
    seed_runtime(catalog, env_id)
    catalog.close()
    patch_from_project(monkeypatch, FakePostgresCluster(mode="external"))
    monkeypatch.setattr(
        EnvironmentMonitor,
        "_collect_runtime",
        lambda *_: (_ for _ in ()).throw(AssertionError("removed runtime must not be collected")),
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.probe_address",
        lambda *_: (_ for _ in ()).throw(AssertionError("removed port must not be probed")),
    )

    env = (
        EnvironmentMonitor(catalog_path=tmp_path / "catalog.sqlite3")
        .snapshot(include_removed=True)
        .environments[0]
    )

    assert env.runtime.state is RuntimeState.STOPPED
    assert env.runtime.http_port is None
    assert env.observed_port is None
