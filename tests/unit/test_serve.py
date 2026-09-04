from __future__ import annotations

import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from odoo_instance_sdk.http import app as serve
from odoo_instance_sdk.models import (
    ClusterContainer,
    ClusterEndpoint,
    ClusterMetrics,
    ClusterSnapshot,
    DatabaseFootprint,
    EnvironmentArtifacts,
    EnvironmentSnapshot,
    EnvironmentState,
    GitActivity,
    GitActivityState,
    PgAdminEligibility,
    PgAdminEligibilityState,
    PidScope,
    PostgresClusterState,
    ProjectSummary,
    PythonEnvFootprint,
    RuntimeMetrics,
    RuntimeState,
    Snapshot,
    StorageFootprint,
)


# This module exercises the built-in FastAPI server.  Core CI deliberately
# installs no dashboard extras; command-level missing-extra behavior is covered
# separately in test_cli_monitor.py.
@pytest.mark.dashboard
def test_dashboard_dependencies_are_installed() -> None:
    import fastapi  # noqa: F401
    import psutil  # noqa: F401
    import uvicorn  # noqa: F401


# --------------------------------------------------------------------- _select_port
def _occupy(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_select_port_explicit_free() -> None:
    # Use an unlikely-to-be-occupied high port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert serve._select_port("127.0.0.1", free) == free


def test_select_port_ipv6_loopback_free() -> None:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
        probe.bind(("::1", 0))
        free = probe.getsockname()[1]
    assert serve._select_port("::1", free) == free


def test_select_port_explicit_occupied() -> None:
    s = _occupy(0)
    occupied = s.getsockname()[1]
    try:
        with pytest.raises(SystemExit) as exc:
            serve._select_port("127.0.0.1", occupied)
        assert str(occupied) in str(exc.value)
    finally:
        s.close()


def test_select_port_none_uses_default_when_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "_is_port_free", lambda _h, p: p == serve._DEFAULT_PORT)
    assert serve._select_port("127.0.0.1", None) == serve._DEFAULT_PORT


def test_select_port_none_scans_when_default_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    free = serve._SCAN_START + 3

    def is_free(_h: str, p: int) -> bool:
        return p == free

    monkeypatch.setattr(serve, "_is_port_free", is_free)
    assert serve._select_port("127.0.0.1", None) == free


def test_select_port_never_uses_8070_8099(monkeypatch: pytest.MonkeyPatch) -> None:
    chosen: list[int] = []

    def is_free(_h: str, p: int) -> bool:
        chosen.append(p)
        return p == 8080  # inside the forbidden range

    monkeypatch.setattr(serve, "_is_port_free", is_free)
    with pytest.raises(SystemExit):
        serve._select_port("127.0.0.1", None)
    # The forbidden range is never probed.
    assert all(p < 8070 or p > 8099 for p in chosen)


def test_select_port_none_all_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "_is_port_free", lambda _h, _p: False)
    with pytest.raises(SystemExit) as exc:
        serve._select_port("127.0.0.1", None)
    assert "8069" in str(exc.value)
    assert "8100-8120" in str(exc.value)


# --------------------------------------------------------------------- import guard
def test_run_server_missing_dashboard_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "uvicorn":
            raise ImportError("no module named 'uvicorn'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as exc:
        serve.run_server(headless=True)
    msg = str(exc.value)
    assert "dashboard" in msg
    assert "pip install odoo-instance-sdk[dashboard]" in msg


@pytest.mark.parametrize("missing", ["fastapi", "uvicorn"])
def test_run_server_missing_each_dashboard_dependency(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == missing:
            raise ImportError(f"no module named {missing}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as exc:
        serve.run_server(headless=True)
    # ``SystemExit(<message>)`` is a process exit status of 1 while retaining
    # the actionable installation hint on stderr.
    assert exc.value.code != 0
    assert "pip install odoo-instance-sdk[dashboard]" in str(exc.value)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.test"])
def test_run_server_rejects_unauthenticated_network_bind(host: str) -> None:
    with pytest.raises(SystemExit, match="loopback"):
        serve.run_server(host=host, headless=True)


# --------------------------------------------------------------------- FastAPI routes
def _client(headless: bool, monitor: Any = None):  # type: ignore[no-untyped-def]
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    return TestClient(
        serve.create_app(headless=headless, monitor=monitor), base_url="http://localhost"
    )


@pytest.mark.dashboard
def test_healthz() -> None:
    with _client(headless=True) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.dashboard
def test_untrusted_host_is_rejected() -> None:
    with _client(headless=True) as client:
        response = client.get("/healthz", headers={"host": "attacker.example"})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["[::1]", "[::1]:8069", "::1"])
@pytest.mark.dashboard
def test_ipv6_loopback_host_is_accepted(host: str) -> None:
    with _client(headless=True) as client:
        response = client.get("/healthz", headers={"host": host})
    assert response.status_code == 200


@pytest.mark.dashboard
def test_snapshot_ok() -> None:
    with _client(headless=True) as client:
        resp = client.get("/api/v1/snapshot")
        assert resp.status_code == 200
        payload = resp.json()
        assert "schema_version" in payload
        assert "projects" in payload
        assert "environments" in payload


@pytest.mark.dashboard
def test_snapshot_reuses_injected_monitor_and_forwards_filter() -> None:
    class Monitor:
        def __init__(self) -> None:
            self.calls: list[str | None] = []

        def snapshot(self, project_id: str | None = None) -> Snapshot:
            self.calls.append(project_id)
            return Snapshot(
                schema_version=3,
                generated_at=datetime.now(UTC),
                projects=(),
                environments=(),
            )

    monitor = Monitor()
    with _client(headless=True, monitor=monitor) as client:
        assert client.get("/api/v1/snapshot?project_id=project_x").status_code == 200
        assert client.get("/api/v1/snapshot").status_code == 200
    assert monitor.calls == ["project_x", None]


@pytest.mark.dashboard
def test_snapshot_has_exact_json_content_type_and_body() -> None:
    class Monitor:
        def snapshot(self, project_id: str | None = None) -> Snapshot:
            assert project_id == "project_x"
            return Snapshot(
                schema_version=3,
                generated_at=datetime(2026, 8, 24, tzinfo=UTC),
                projects=(
                    ProjectSummary(
                        id="project_x",
                        name="x",
                        display_hint="x",
                        environment_count=1,
                        cluster=ClusterSnapshot(
                            mode="compose",
                            owned=True,
                            state=PostgresClusterState.HEALTHY,
                            endpoint=ClusterEndpoint(host="127.0.0.1", port=5432),
                            container=ClusterContainer(
                                id="container",
                                name="postgres",
                                image="postgres:16",
                                pid=7,
                                pid_scope=PidScope.DOCKER_VM,
                            ),
                            metrics=ClusterMetrics(
                                cpu_percent=2.5,
                                memory_usage_bytes=1,
                                memory_limit_bytes=2,
                                volume_usage_bytes=None,
                                sampled_at=None,
                            ),
                            unavailability_reason=None,
                            sampled_at=None,
                        ),
                        runtime=None,
                    ),
                ),
                environments=(
                    EnvironmentSnapshot(
                        id="env_x",
                        project_id="project_x",
                        name="x",
                        branch="main",
                        short_sha="abc1234",
                        db_mode="shared",
                        database="db_x",
                        lifecycle_state=EnvironmentState.READY,
                        allocated_http_port=8069,
                        observed_port=None,
                        artifacts=EnvironmentArtifacts(
                            worktree_exists=True,
                            worktree_registered=True,
                            config_exists=True,
                            python_exists=True,
                            python_contained=True,
                            dependency_lock_exists=True,
                            backup_exists=None,
                        ),
                        runtime=RuntimeMetrics(
                            state=RuntimeState.READY,
                            root_pid=1,
                            child_pids=(2, 3),
                            process_count=3,
                            cpu_percent=1.5,
                            rss_bytes=1024,
                            started_at=None,
                            http_url="http://127.0.0.1:8069",
                            http_port=8069,
                            database_name="db_x",
                            commit_sha="abc",
                            branch="main",
                        ),
                        git=GitActivity(
                            default_branch="main",
                            head_sha="abc",
                            short_sha="abc",
                            branch="main",
                            ahead=0,
                            behind=0,
                            diff=None,
                            state=GitActivityState.CLEAN,
                        ),
                        storage=StorageFootprint(
                            total_bytes=3,
                            complete=True,
                            worktree_bytes=1,
                            python_environment=PythonEnvFootprint(owned=True, bytes=1),
                            database=DatabaseFootprint(
                                owned=False,
                                postgres_bytes=None,
                                filestore_bytes=None,
                                total_bytes=None,
                            ),
                            other_files_bytes=1,
                        ),
                        pgadmin=PgAdminEligibility(state=PgAdminEligibilityState.ELIGIBLE),
                    ),
                ),
            )

    with _client(headless=True, monitor=Monitor()) as client:
        response = client.get("/api/v1/snapshot?project_id=project_x")
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    payload["generated_at"] = "<generated_at>"
    assert payload == {
        "schema_version": 3,
        "generated_at": "<generated_at>",
        "projects": [
            {
                "id": "project_x",
                "name": "x",
                "display_hint": "x",
                "environment_count": 1,
                "runtime": None,
                "cluster": {
                    "mode": "compose",
                    "owned": True,
                    "state": "healthy",
                    "endpoint": {"host": "127.0.0.1", "port": 5432},
                    "container": {
                        "id": "container",
                        "name": "postgres",
                        "image": "postgres:16",
                        "pid": 7,
                        "pid_scope": "docker_vm",
                    },
                    "metrics": {
                        "cpu_percent": 2.5,
                        "memory_usage_bytes": 1,
                        "memory_limit_bytes": 2,
                        "volume_usage_bytes": None,
                        "sampled_at": None,
                    },
                    "unavailability_reason": None,
                    "sampled_at": None,
                    "server": None,
                    "server_unavailability_reason": None,
                },
            }
        ],
        "environments": [
            {
                "id": "env_x",
                "project_id": "project_x",
                "name": "x",
                "branch": "main",
                "short_sha": "abc1234",
                "db_mode": "shared",
                "database": "db_x",
                "lifecycle_state": "ready",
                "allocated_http_port": 8069,
                "observed_port": None,
                "artifacts": {
                    "worktree_exists": True,
                    "worktree_registered": True,
                    "config_exists": True,
                    "python_exists": True,
                    "python_contained": True,
                    "dependency_lock_exists": True,
                    "backup_exists": None,
                },
                "runtime": {
                    "state": "ready",
                    "root_pid": 1,
                    "child_pids": [2, 3],
                    "process_count": 3,
                    "cpu_percent": 1.5,
                    "rss_bytes": 1024,
                    "started_at": None,
                    "http_url": "http://127.0.0.1:8069",
                    "http_port": 8069,
                    "database_name": "db_x",
                    "commit_sha": "abc",
                    "branch": "main",
                },
                "git": {
                    "default_branch": "main",
                    "head_sha": "abc",
                    "short_sha": "abc",
                    "branch": "main",
                    "ahead": 0,
                    "behind": 0,
                    "diff": None,
                    "state": "clean",
                },
                "storage": {
                    "total_bytes": 3,
                    "complete": True,
                    "worktree_bytes": 1,
                    "python_environment": {"owned": True, "bytes": 1},
                    "database": {
                        "owned": False,
                        "postgres_bytes": None,
                        "filestore_bytes": None,
                        "total_bytes": None,
                    },
                    "other_files_bytes": 1,
                },
                "pgadmin": {"state": "eligible"},
            }
        ],
    }


@pytest.mark.dashboard
def test_snapshot_project_filter_unknown() -> None:
    with _client(headless=True) as client:
        resp = client.get("/api/v1/snapshot?project_id=does-not-exist")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["projects"] == []
        assert payload["environments"] == []


@pytest.mark.dashboard
def test_snapshot_legacy_monitor_error_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from odoo_instance_sdk.exceptions import MonitorError
    from odoo_instance_sdk.resources import monitor as monitor_mod

    def boom(self: Any, project_id: str | None = None) -> Any:
        raise MonitorError("boom-with-secret-/abs/path")

    monkeypatch.setattr(monitor_mod.EnvironmentMonitor, "snapshot", boom)
    with _client(headless=True) as client:
        resp = client.get("/api/v1/snapshot")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "monitor_snapshot_failed"
        assert body["message"] == "monitor snapshot failed"
        # Redacted: no secrets/paths leak.
        assert "/abs/path" not in resp.text
        assert "boom" not in resp.text


@pytest.mark.dashboard
def test_snapshot_monitor_error_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from odoo_instance_sdk.exceptions import MonitorError
    from odoo_instance_sdk.resources import monitor as monitor_mod

    def boom(self: Any, project_id: str | None = None) -> Any:
        raise MonitorError("process metrics unavailable")

    monkeypatch.setattr(monitor_mod.EnvironmentMonitor, "snapshot", boom)
    with _client(headless=True) as client:
        resp = client.get("/api/v1/snapshot")
        assert resp.status_code == 500
        body = resp.json()
        assert body == {
            "code": "monitor_snapshot_failed",
            "message": "monitor snapshot failed",
        }


@pytest.mark.dashboard
def test_headless_no_static_mount() -> None:
    with _client(headless=True) as client:
        # No SPA mount in headless mode: "/" is not a static file.
        resp = client.get("/")
        assert resp.status_code == 404


@pytest.mark.dashboard
def test_ui_no_dist_fails_actionably(monkeypatch: pytest.MonkeyPatch) -> None:
    """UI mode must never silently become an API-only service."""
    monkeypatch.setattr(serve, "_WEB_DIST", Path("/nonexistent/dist-xyz"))
    with pytest.raises(RuntimeError, match="SPA assets are missing"):
        serve.create_app(headless=False)
    with _client(headless=True) as client:
        assert client.get("/healthz").status_code == 200
