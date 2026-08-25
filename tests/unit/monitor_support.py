"""Typed fakes and catalog builders shared by monitor subsystem tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from odoo_instance_sdk.internal.process_metrics import CpuPoint, ProcessTreeResult
from odoo_instance_sdk.models import (
    ClusterResourceSnapshot,
    GitActivity,
    GitActivityState,
    PostgresClusterState,
)
from odoo_instance_sdk.resources.postgres import PostgresCluster
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def make_env(env_id: str, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "id": env_id,
        "name": "test",
        "repository_root": "/repo",
        "git_common_dir": "/repo/.git",
        "branch": "main",
        "base_ref": "HEAD",
        "worktree_path": "/wt",
        "generated_config_path": "/wt/odoo.conf",
        "python_environment_path": "/venv",
        "python_environment_owned": False,
        "dependency_lock_path": "/lock",
        "db_mode": "shared",
        "source_db_name": "mydb",
        "target_db_name": None,
        "backup_id": None,
        "runtime_json": "{}",
        "state": "ready",
        "created_at": "2026-01-01T00:00:00",
        "last_used_at": None,
        "removed_at": None,
        "last_error": None,
    }
    result.update(overrides)
    return result


def runtime_kwargs(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "root_pid": 12345,
        "create_time": 1700000000.0,
        "started_at": "2026-01-01T00:00:00",
        "checkout_branch": "main",
        "commit_sha": "abc123def456",
        "http_url": "http://127.0.0.1:8069",
        "http_port": 8069,
        "database_name": "mydb",
    }
    result.update(overrides)
    return result


def make_catalog(tmp_path: Path) -> BackupCatalog:
    return BackupCatalog(db_path=tmp_path / "catalog.sqlite3")


def seed_env(catalog: BackupCatalog, env: dict[str, object]) -> None:
    catalog.create_environment(env)


def seed_runtime(catalog: BackupCatalog, env_id: str, **kwargs: object) -> None:
    catalog.upsert_environment_runtime(env_id, **runtime_kwargs(**kwargs))  # type: ignore[arg-type]


class FakeProcessProvider:
    def __init__(self, *, result: ProcessTreeResult | None = None) -> None:
        self.calls = 0
        self.result = result

    def collect(
        self, root_pid: int, create_time: float, *, prev_cpu_point: CpuPoint | None
    ) -> tuple[ProcessTreeResult, CpuPoint] | None:
        self.calls += 1
        if self.result is None:
            return None
        return self.result, CpuPoint(times_cpu=0.0, timestamp=0.0)


class FakeGitProvider:
    def __init__(self, *, result: GitActivity | None = None, raise_ex: bool = False) -> None:
        self.calls = 0
        self.result = result or GitActivity(
            default_branch="main",
            head_sha="abcdef1234567890",
            short_sha="abcdef1",
            branch="main",
            ahead=0,
            behind=0,
            diff=None,
            state=GitActivityState.CLEAN,
        )
        self.raise_ex = raise_ex

    def collect(self, worktree: Path) -> GitActivity:
        self.calls += 1
        if self.raise_ex:
            raise RuntimeError("git failed")
        return self.result


class FakeDockerProvider:
    def __init__(self, *, result: ClusterResourceSnapshot | None = None) -> None:
        self.calls = 0
        self.result = result

    def collect(self, **_: object) -> ClusterResourceSnapshot:
        self.calls += 1
        return self.result or ClusterResourceSnapshot(
            container=None, metrics=None, unavailability_reason="stats_failed", sampled_at=None
        )


class FakePostgresCluster:
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
        self._mode, self._endpoint_host, self._endpoint_port = mode, endpoint_host, endpoint_port
        self._state, self._resource, self._project_id = state, resource, project_id
        self._compose_runner: object | None = None
        self._calls = 0

    @property
    def mode(self) -> str:
        return self._mode

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
    def compose_runner(self) -> object:
        assert self._compose_runner is not None
        return self._compose_runner

    @property
    def compose_file(self) -> Path:
        return Path("/fake/compose.yaml")

    def status(self) -> PostgresClusterState:
        self._calls += 1
        return self._state


def patch_from_project(
    monkeypatch: pytest.MonkeyPatch,
    cluster: FakePostgresCluster | None = None,
    *,
    raise_ex: type[BaseException] | None = None,
) -> None:
    def from_project(project_path: str | Path, **kwargs: object) -> PostgresCluster:
        if raise_ex is not None:
            raise raise_ex(str(project_path))
        return cluster  # type: ignore[return-value]

    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(from_project))


def write_odoo_conf(path: Path, *, http_port: int = 8069) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[options]\nhttp_port = {http_port}\nhttp_interface = 127.0.0.1\ndata_dir = /tmp/odoo_data\n",
        encoding="utf-8",
    )
