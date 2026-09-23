"""Internal factory for constructing Odoo HTTP clients.

Resources and tests substitute this seam instead of patching ``httpx`` directly.
"""

from __future__ import annotations

from odoo_instance_sdk.internal.transport.odoo import OdooHttpClient


def open_odoo_http_client(base_url: str, *, timeout: float | None = None) -> OdooHttpClient:
    """Return a client for one Odoo origin."""
    return OdooHttpClient.for_origin(base_url, timeout=timeout)


__all__ = ["open_odoo_http_client"]
