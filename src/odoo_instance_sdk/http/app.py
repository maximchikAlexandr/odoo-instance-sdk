from __future__ import annotations

import secrets
import socket
import webbrowser
from pathlib import Path
from typing import Any

from odoo_instance_sdk.http.monitor import (
    PgAdminOpener,
    SnapshotProvider,
    build_monitor_router,
    build_pgadmin_router,
    install_openapi_schema,
)

__all__ = ["create_app", "run_server"]

_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8069
_SCAN_START = 8100
_SCAN_END = 8120
_CSRF_COOKIE = "odoo_instance_sdk_csrf"


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


def create_app(  # noqa: C901
    *,
    headless: bool,
    monitor: SnapshotProvider | None = None,
    static_assets: bool = True,
    pgadmin_opener: PgAdminOpener | None = None,
) -> Any:
    """Build the FastAPI app while keeping dashboard dependencies optional."""
    from fastapi import FastAPI, Request
    from fastapi.responses import Response

    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    app_monitor = monitor if monitor is not None else EnvironmentMonitor()
    app = FastAPI(title="odoo-instance-sdk monitor")

    async def loopback_host_only(request: Request, call_next: Any) -> Any:
        # TrustedHostMiddleware cannot accept the standards-compliant
        # ``[::1]:port`` form, so parse IPv6 brackets explicitly.
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

    app.middleware("http")(loopback_host_only)

    async def issue_csrf_session_cookie(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        # The browser reads this non-HttpOnly double-submit token and sends it
        # in X-CSRF-Token for the one state-changing dashboard operation.
        # Tokens are created only on safe requests; rejected POSTs cannot mint
        # a session token as a side effect.
        if request.method in {"GET", "HEAD"} and _CSRF_COOKIE not in request.cookies:
            response.set_cookie(
                _CSRF_COOKIE,
                secrets.token_urlsafe(32),
                path="/",
                secure=False,
                httponly=False,
                samesite="strict",
            )
        return response

    app.middleware("http")(issue_csrf_session_cookie)

    app.include_router(build_monitor_router(app_monitor))

    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.get("/healthz")(healthz)

    install_openapi_schema(app)

    if not headless:
        if pgadmin_opener is None:
            from odoo_instance_sdk.client import OdooClient
            from odoo_instance_sdk.config import OdooClientConfig

            # Keep one client/resource graph alive for the application.  The
            # injected path used by schema export remains entirely side-effect
            # free and does not construct this runtime resource.
            client = OdooClient(config=OdooClientConfig(executable="odoo"))
            app.state.odoo_client = client
            pgadmin_opener = client.environments.open_pgadmin
        app.include_router(build_pgadmin_router(pgadmin_opener))
        if static_assets:
            if not _WEB_DIST.is_dir():
                raise RuntimeError(
                    "monitor SPA assets are missing; reinstall a package built with the dashboard assets "
                    "or run `npm ci && npm run build` in src/odoo_instance_sdk/web"
                )
            from fastapi.staticfiles import StaticFiles

            app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="spa")

    return app


def _is_loopback_host(host: str) -> bool:
    return host.lower() == "localhost" or host in {"127.0.0.1", "::1"}


def run_server(
    *,
    host: str = _DEFAULT_HOST,
    port: int | None = None,
    headless: bool = False,
    no_open: bool = False,
) -> None:
    """Start the monitor server; dashboard dependencies are imported lazily."""
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
        url_host = f"[{host}]" if ":" in host else host
        webbrowser.open(f"http://{url_host}:{chosen}/")

    uvicorn.run(app, host=host, port=chosen)
