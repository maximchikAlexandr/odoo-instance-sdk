"""Local-only instance guard for destructive database operations (restore, drop)."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import RemoteInstanceError


def assert_local(base_url: str) -> None:
    parsed = urlsplit(base_url)
    hostname = parsed.hostname
    if hostname is None:
        raise RemoteInstanceError(f"Cannot determine hostname from URL: {base_url}")
    if hostname.lower() == "localhost":
        return
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return
    except ValueError:
        pass
    raise RemoteInstanceError(
        f"Operation not allowed on remote instance: {hostname}. "
        f"Only localhost, 127.0.0.0/8, and ::1 are permitted."
    )
