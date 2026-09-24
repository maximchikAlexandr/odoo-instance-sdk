"""Internal HTTP transport for Odoo.

Lazily imported; ``httpx`` stays absent after ``import odoo_instance_sdk.cli``.
"""

from __future__ import annotations

from odoo_instance_sdk.internal.transport.base import (
    BaseHttpClient,
    HttpClient,
    StreamingResponse,
    TransportError,
    TransportProtocolError,
    TransportStatusError,
    TransportUnavailableError,
)
from odoo_instance_sdk.internal.transport.odoo import OdooHttpClient

__all__ = [
    "BaseHttpClient",
    "HttpClient",
    "OdooHttpClient",
    "StreamingResponse",
    "TransportError",
    "TransportProtocolError",
    "TransportStatusError",
    "TransportUnavailableError",
]
