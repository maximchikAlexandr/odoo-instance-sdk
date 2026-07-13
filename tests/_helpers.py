"""Shared test helpers."""

from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Add src/ to sys.path so tests can be run directly with `python tests/test_*.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odoo_instance_sdk import OdooClient, OdooClientConfig


def make_client(base_url: str = "http://localhost:8069") -> OdooClient:
    """Create a test OdooClient with sensible defaults."""
    config = OdooClientConfig(
        executable=sys.executable,
        base_url=base_url,
        master_pwd="admin",
    )
    return OdooClient(config)


class SilentHandler(BaseHTTPRequestHandler):
    """BaseHTTPRequestHandler that silences default log output."""

    def log_message(self, *args: object) -> None:
        pass


def start_stub_server(
    handler_cls: type[BaseHTTPRequestHandler],
    *,
    databases: list[str] | None = None,
) -> tuple[HTTPServer, int]:
    """Start a stub HTTP server on a free port. Returns (server, port).

    If `databases` is provided, a fresh handler subclass is created with the
    given list bound as a class attribute. This avoids state leaking across
    tests when the handler mutates per-request state.
    """
    if databases is not None:
        handler_cls = type(
            handler_cls.__name__, (handler_cls,), {"databases": list(databases)}
        )
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port
