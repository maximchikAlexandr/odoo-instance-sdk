from __future__ import annotations

import socket
import webbrowser
from pathlib import Path

__all__ = ["create_app", "run_server"]

_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8069
_SCAN_START = 8100
_SCAN_END = 8120


def _is_port_free(host: str, port: int) -> bool:
    """Best-effort bind test. SO_REUSEADDR; race possible (spec accepts)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
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


def create_app(*, headless: bool):  # type: ignore[no-untyped-def]
    """Build the FastAPI app. Imports fastapi lazily (dashboard extra).

    headless=True: API routes only, no static mount.
    headless=False: API routes + StaticFiles mount at ``/`` if ``web/dist`` exists.
    """
    import msgspec
    from fastapi import FastAPI, Query
    from fastapi.responses import JSONResponse, Response

    from odoo_instance_sdk.exceptions import MonitorError, MonitorExtrasMissingError
    from odoo_instance_sdk.resources.monitor import EnvironmentMonitor

    app = FastAPI(title="odoo-instance-sdk monitor")

    @app.get("/api/v1/snapshot")  # type: ignore[untyped-decorator]
    def snapshot(project_id: str | None = Query(default=None)) -> Response:
        monitor = EnvironmentMonitor()
        try:
            snap = monitor.snapshot(project_id=project_id)
        except MonitorExtrasMissingError as exc:
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"metrics extra required: pip install odoo-instance-sdk[metrics] ({exc})"
                },
            )
        except MonitorError:
            # ponytail: redacted — never leak paths/secrets; the dashboard only
            # needs to signal failure, not diagnose it.
            return JSONResponse(status_code=500, content={"error": "monitor snapshot failed"})
        body = msgspec.json.encode(snap)
        return Response(content=body, media_type="application/json")

    @app.get("/healthz")  # type: ignore[untyped-decorator]
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if not headless and _WEB_DIST.is_dir():
        from fastapi.staticfiles import StaticFiles

        # ponytail: API routes registered first take precedence over the SPA
        # catch-all mount at "/".
        app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="spa")

    return app


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
    try:
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
        webbrowser.open(f"http://{host}:{chosen}/")

    uvicorn.run(app, host=host, port=chosen)
