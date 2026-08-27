"""Opt-in smoke test for monitor snapshot + headless API across multiple projects.

Requires core psutil and the dashboard FastAPI extra. Skips gracefully when
FastAPI is missing.
Run with: ``pytest -m integration tests/integration/test_monitor_smoke.py``
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from odoo_instance_sdk.resources.monitor import EnvironmentMonitor
from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

pytestmark = pytest.mark.integration


def _seed_env(
    catalog: BackupCatalog,
    *,
    name: str,
    repository_root: str,
    git_common_dir: str,
    worktree_path: str,
    branch: str = "main",
    state: str = "ready",
) -> str:
    env_id = str(uuid.uuid4())
    catalog.create_environment(
        {
            "id": env_id,
            "name": name,
            "repository_root": repository_root,
            "git_common_dir": git_common_dir,
            "branch": branch,
            "base_ref": "HEAD",
            "worktree_path": worktree_path,
            "generated_config_path": f"{worktree_path}/odoo.conf",
            "python_environment_path": f"{worktree_path}/.venv",
            "python_environment_owned": False,
            "dependency_lock_path": f"{worktree_path}/requirements.lock",
            "db_mode": "shared",
            "source_db_name": "demo",
            "target_db_name": None,
            "backup_id": None,
            "runtime_json": "{}",
            "state": state,
            "created_at": "2026-01-01T00:00:00",
            "last_used_at": None,
            "removed_at": None,
            "last_error": None,
        }
    )
    return env_id


@pytest.mark.serial
def test_monitor_headless_api_multi_project_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("psutil")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.internal import serve

    catalog_path = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr(
        "odoo_instance_sdk.resources.monitor.get_catalog_path",
        lambda: catalog_path,
    )
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.paths.get_catalog_path",
        lambda: catalog_path,
    )

    catalog = BackupCatalog(db_path=catalog_path)
    repo_a = tmp_path / "project-a"
    repo_b = tmp_path / "project-b"
    for repo in (repo_a, repo_b):
        repo.mkdir()
        (repo / ".git").mkdir()
        wt = repo / "wt-main"
        wt.mkdir()
        (wt / "odoo.conf").write_text("[options]\nhttp_port = 8069\n")

    _seed_env(
        catalog,
        name="a-main",
        repository_root=str(repo_a),
        git_common_dir=str(repo_a / ".git"),
        worktree_path=str(repo_a / "wt-main"),
    )
    _seed_env(
        catalog,
        name="a-stopped",
        repository_root=str(repo_a),
        git_common_dir=str(repo_a / ".git"),
        worktree_path=str(repo_a / "wt-stopped"),
        branch="stopped",
        state="ready",
    )
    _seed_env(
        catalog,
        name="b-main",
        repository_root=str(repo_b),
        git_common_dir=str(repo_b / ".git"),
        worktree_path=str(repo_b / "wt-main"),
    )
    catalog.close()

    monitor = EnvironmentMonitor(catalog_path=catalog_path)
    snapshot = monitor.snapshot()
    assert len(snapshot.projects) == 2
    assert len(snapshot.environments) == 3
    stopped = [env for env in snapshot.environments if env.name == "a-stopped"]
    assert stopped and stopped[0].runtime.state.value == "stopped"

    with TestClient(serve.create_app(headless=True), base_url="http://localhost") as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        resp = client.get("/api/v1/snapshot")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["schema_version"] == 2
        assert len(payload["projects"]) == 2
        assert len(payload["environments"]) == 3

        ready_urls = [
            env["runtime"]["http_url"]
            for env in payload["environments"]
            if env["runtime"]["state"] == "ready" and env["runtime"].get("http_url")
        ]
        assert ready_urls == []

    assert catalog_path.is_file()
