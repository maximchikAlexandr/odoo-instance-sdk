from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path
from typing import Any, Protocol

__all__ = ["create_app", "run_server"]

_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8069
_SCAN_START = 8100
_SCAN_END = 8120


class _SnapshotProvider(Protocol):
    def snapshot(self, project_id: str | None = None) -> object: ...


def _is_port_free(host: str, port: int) -> bool:
    """Best-effort bind test. SO_REUSEADDR; race possible (spec accepts)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
        return True


def _select_port(host: str, port: int | None) -> int:
    if port is not None:
        if _is_port_free(host, port):
            return port
        raise SystemExit(f"port {port} is already in use")
    if _is_port_free(host, _DEFAULT_PORT):
        return _DEFAULT_PORT
    for candidate in range(_SCAN_START, _SCAN_END + 1):
        if _is_port_free(host, candidate):
            return candidate
    raise SystemExit("no free port in 8069, 8100-8120; specify --port")


def create_app(*, headless: bool, monitor: _SnapshotProvider | None = None) -> Any:
    """Build the FastAPI app. Imports fastapi lazily (dashboard extra).

    headless=True: API routes only, no static mount.
    headless=False: API routes + packaged StaticFiles mount at ``/``.
    """
    import msgspec
    from fastapi import FastAPI, Query, Request
    from fastapi.responses import JSONResponse, Response

    from odoo_instance_sdk.exceptions import MonitorError
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    # Keep the monitor (and its bounded caches) app-scoped rather than recreating
    # it for every request. A lock also prevents concurrent polling requests from
    # multiplying expensive Docker/process/git probes.
    app_monitor = monitor if monitor is not None else EnvironmentMonitor()
    snapshot_lock = threading.Lock()
    app = FastAPI(title="odoo-instance-sdk monitor")

    @app.middleware("http")
    async def loopback_host_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Starlette's TrustedHostMiddleware strips the port by splitting at the
        # first colon, so it cannot accept the standards-compliant `[::1]:port`.
        # Parse IPv6 brackets ourselves while keeping the allow-list exact.
        raw = request.headers.get("host", "").lower()
        if raw.startswith("["):
            end = raw.find("]")
            suffix = raw[end + 1 :] if end > 0 else "invalid"
            host = raw[1:end] if suffix == "" or suffix.startswith(":") else ""
        elif raw == "::1":
            host = raw
        else:
            host = raw.split(":", 1)[0]
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return Response(content="Invalid host header", status_code=400)
        return await call_next(request)

    @app.get("/api/v1/snapshot")
    def snapshot(project_id: str | None = Query(default=None)) -> Response:
        try:
            with snapshot_lock:
                snap = app_monitor.snapshot(project_id=project_id)
        except MonitorError:
            # ponytail: redacted — never leak paths/secrets; the dashboard only
            # needs to signal failure, not diagnose it.
            return JSONResponse(status_code=500, content={"error": "monitor snapshot failed"})
        body = msgspec.json.encode(snap)
        return Response(content=body, media_type="application/json")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if not headless:
        if not _WEB_DIST.is_dir():
            raise RuntimeError(
                "monitor SPA assets are missing; reinstall a package built with the dashboard assets "
                "or run `npm ci && npm run build` in src/odoo_instance_sdk/web"
            )
        from fastapi.staticfiles import StaticFiles

        # ponytail: API routes registered first take precedence over the SPA
        # catch-all mount at "/".
        app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="spa")

    return app


def _is_loopback_host(host: str) -> bool:
    """Return whether ``host`` is a local-only bind target.

    The dashboard intentionally has no authentication because it exposes local
    operational metadata. Do not accidentally turn it into a network service.
    """
    return host.lower() == "localhost" or host in {"127.0.0.1", "::1"}


def run_server(
    *,
    host: str = _DEFAULT_HOST,
    port: int | None = None,
    headless: bool = False,
    no_open: bool = False,
) -> None:
    """Start the monitor FastAPI server. Imports fastapi/uvicorn lazily.

    Exits with code 1 and an actionable hint if the dashboard extra is missing.
    """
    if not _is_loopback_host(host):
        raise SystemExit(
            "monitor command only supports loopback hosts; refusing unauthenticated network bind"
        )
    try:
        import fastapi  # noqa: F401
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            f"monitor command requires the dashboard extra: "
            f"pip install odoo-instance-sdk[dashboard] ({exc})"
        ) from exc

    chosen = _select_port(host, port)
    app = create_app(headless=headless)

    if not headless and not no_open:
        # ponytail: open before run; the browser retries while uvicorn boots.
        url_host = f"[{host}]" if ":" in host else host
        webbrowser.open(f"http://{url_host}:{chosen}/")

    uvicorn.run(app, host=host, port=chosen)
