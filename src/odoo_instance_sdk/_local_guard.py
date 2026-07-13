"""Local-only instance guard for destructive database operations (restore, drop)."""

from __future__ import annotations

import ipaddress
import warnings
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import RemoteInstanceError

_cleartext_warned = False


def is_local_host(hostname: str | None) -> bool:
    """True if hostname is localhost or a loopback IP. None/empty is NOT local."""
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return True
    except ValueError:
        pass
    return False


def assert_local(base_url: str) -> None:
    parsed = urlsplit(base_url)
    hostname = parsed.hostname
    if hostname is None:
        raise RemoteInstanceError(f"Cannot determine hostname from URL: {base_url}")
    if is_local_host(hostname):
        return
    raise RemoteInstanceError(
        f"Operation not allowed on remote instance: {hostname}. "
        f"Only localhost, 127.0.0.0/8, and ::1 are permitted."
    )


def warn_if_cleartext_auth(base_url: str, *, stacklevel: int) -> None:
    """Warn if Basic Auth will be sent over unencrypted HTTP to a non-local host. Fires once per process."""
    global _cleartext_warned  # noqa: PLW0603 - module-level once-per-process sentinel
    if _cleartext_warned:
        return
    p = urlsplit(base_url)
    if p.scheme == "http" and not is_local_host(p.hostname):
        warnings.warn(
            "Using Basic Auth over unencrypted HTTP for non-local instance — "
            "credentials will be transmitted in cleartext. Use HTTPS.",
            stacklevel=stacklevel,
        )
        _cleartext_warned = True
