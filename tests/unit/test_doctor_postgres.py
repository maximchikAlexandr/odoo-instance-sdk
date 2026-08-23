from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk.client import OdooClient
from odoo_instance_sdk.config import OdooClientConfig
from odoo_instance_sdk.internal.doctor import run_doctor
from odoo_instance_sdk.models import PostgresClusterState
from odoo_instance_sdk.project import PostgresProjectConfig, ProjectConfig


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _write_project(
    tmp_path: Path, *, mode: str = "compose", source_config: Path | None = None
) -> Path:
    _git_init(tmp_path)
    manifest_dir = tmp_path / ".odcli"
    manifest_dir.mkdir()
    postgres: PostgresProjectConfig | None = None
    if mode == "compose":
        postgres = PostgresProjectConfig(mode="compose", image="pg", port=5468, user="odoo")
    cfg = ProjectConfig(
        repository_root=tmp_path,
        odoo_bin=Path("/opt/odoo/odoo-bin"),
        source_config=source_config,
        postgres=postgres,
    )
    (manifest_dir / "project.toml").write_text(cfg.to_manifest())
    return tmp_path


def _write_source_config(tmp_path: Path, *, host: str = "127.0.0.1", port: int = 5432) -> Path:
    p = tmp_path / "odoo.conf"
    p.write_text(f"[options]\ndb_host = {host}\ndb_port = {port}\n")
    return p


def _make_client() -> OdooClient:
    return OdooClient(config=OdooClientConfig(executable="odoo"))


@pytest.fixture(autouse=True)
def _patch_cluster_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch PostgresCluster.from_project to use a fake status (read-only)."""
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    state_for_test: dict[str, PostgresClusterState] = {"value": PostgresClusterState.HEALTHY}

    class _FakeCluster:
        def __init__(self, project_path: str | Path, *, compose_runner: Any = None) -> None:
            self._mode = "compose"
            self._endpoint = "127.0.0.1:5468"

        @property
        def mode(self) -> str:
            return self._mode

        @property
        def owned(self) -> bool:
            return self._mode == "compose"

        @property
        def endpoint(self) -> str:
            return self._endpoint

        def status(self) -> PostgresClusterState:
            return state_for_test["value"]

        def to_diagnostic_dict(self) -> dict[str, object]:
            return {"mode": self._mode, "owned": self.owned, "endpoint": self.endpoint}

    monkeypatch.setattr(PostgresCluster, "from_project", staticmethod(_FakeCluster))


@pytest.mark.unit
def test_doctor_reports_postgres_cluster(tmp_path: Path) -> None:
    root = _write_project(tmp_path)
    report = run_doctor(_make_client(), root)
    pg_check = [c for c in report.checks if c.name == "postgres.cluster"]
    assert len(pg_check) == 1
    assert "mode=compose" in pg_check[0].detail
    assert "state=healthy" in pg_check[0].detail
    assert pg_check[0].status == "ok"


@pytest.mark.unit
def test_doctor_no_project_skips_postgres(tmp_path: Path) -> None:
    report = run_doctor(_make_client(), None)
    pg_check = [c for c in report.checks if c.name == "postgres.cluster"]
    assert pg_check == []


@pytest.mark.unit
def test_doctor_warns_when_docker_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_project(tmp_path)
    monkeypatch.setattr("odoo_instance_sdk.internal.doctor.docker_available", lambda: False)
    report = run_doctor(_make_client(), root)
    compose_check = [c for c in report.checks if c.name == "postgres.compose"]
    assert len(compose_check) == 1
    assert compose_check[0].status == "warn"
    assert "docker not found" in compose_check[0].detail.lower()


@pytest.mark.unit
def test_doctor_does_not_start_cluster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_project(tmp_path)
    started: dict[str, bool] = {"called": False}
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    def fake_ensure(self: PostgresCluster, timeout: float = 60.0) -> None:
        started["called"] = True

    monkeypatch.setattr(PostgresCluster, "ensure_running", fake_ensure)
    run_doctor(_make_client(), root)
    assert started["called"] is False
