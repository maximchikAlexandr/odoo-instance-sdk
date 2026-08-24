from __future__ import annotations

import socket
from typing import Any

import pytest

from odoo_instance_sdk.internal import serve


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


# --------------------------------------------------------------------- FastAPI routes
def _client(headless: bool):  # type: ignore[no-untyped-def]
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    return TestClient(serve.create_app(headless=headless))


def test_healthz() -> None:
    with _client(headless=True) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_snapshot_ok() -> None:
    with _client(headless=True) as client:
        resp = client.get("/api/v1/snapshot")
        assert resp.status_code == 200
        payload = resp.json()
        assert "schema_version" in payload
        assert "projects" in payload
        assert "environments" in payload


def test_snapshot_project_filter_unknown() -> None:
    with _client(headless=True) as client:
        resp = client.get("/api/v1/snapshot?project_id=does-not-exist")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["projects"] == []
        assert payload["environments"] == []


def test_snapshot_monitor_error_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from odoo_instance_sdk.exceptions import MonitorError
    from odoo_instance_sdk.resources import monitor as monitor_mod

    def boom(self: Any, project_id: str | None = None) -> Any:
        raise MonitorError("boom-with-secret-/abs/path")

    monkeypatch.setattr(monitor_mod.EnvironmentMonitor, "snapshot", boom)
    with _client(headless=True) as client:
        resp = client.get("/api/v1/snapshot")
        assert resp.status_code == 500
        body = resp.json()
        assert "error" in body
        # Redacted: no secrets/paths leak.
        assert "/abs/path" not in body["error"]
        assert "boom" not in body["error"]


def test_snapshot_extras_missing_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from odoo_instance_sdk.exceptions import MonitorExtrasMissingError
    from odoo_instance_sdk.resources import monitor as monitor_mod

    def boom(self: Any, project_id: str | None = None) -> Any:
        raise MonitorExtrasMissingError("psutil missing")

    monkeypatch.setattr(monitor_mod.EnvironmentMonitor, "snapshot", boom)
    with _client(headless=True) as client:
        resp = client.get("/api/v1/snapshot")
        assert resp.status_code == 500
        body = resp.json()
        assert "metrics" in body["error"]
        assert "pip install odoo-instance-sdk[metrics]" in body["error"]


def test_headless_no_static_mount() -> None:
    with _client(headless=True) as client:
        # No SPA mount in headless mode: "/" is not a static file.
        resp = client.get("/")
        assert resp.status_code == 404


def test_ui_no_dist_skips_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    # In this dev env web/dist does not exist; create_app(headless=False) must
    # still build and serve API routes without mounting static.
    monkeypatch.setattr(serve, "_WEB_DIST", serve.Path("/nonexistent/dist-xyz"))
    with _client(headless=False) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        resp_root = client.get("/")
        assert resp_root.status_code == 404
