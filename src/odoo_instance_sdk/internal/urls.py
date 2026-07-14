from __future__ import annotations

import ipaddress
import warnings
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import InvalidBaseUrlError, NonLocalInstanceError

_cleartext_warned: list[bool] = [False]


def normalize_base_url(raw: str) -> str:
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidBaseUrlError(f"Unsupported scheme: {scheme!r} — only http/https allowed")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidBaseUrlError("Credentials in URL are not allowed")
    if parsed.query:
        raise InvalidBaseUrlError("Query parameters in URL are not allowed")
    if parsed.fragment:
        raise InvalidBaseUrlError("Fragment in URL is not allowed")
    hostname = parsed.hostname
    if hostname is None:
        raise InvalidBaseUrlError(f"Cannot parse hostname from URL: {raw!r}")
    hostname = hostname.lower()
    port = parsed.port
    if port is not None and (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        port = None
    if ":" in hostname:
        netloc = f"[{hostname}]:{port}" if port is not None else f"[{hostname}]"
    else:
        netloc = f"{hostname}:{port}" if port is not None else hostname
    path = parsed.path.rstrip("/")
    if path not in ("", "/"):
        raise InvalidBaseUrlError(f"Path in base URL is not allowed: {path!r}")
    return f"{scheme}://{netloc}"


def is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def assert_local(base_url: str) -> None:
    parsed = urlsplit(base_url)
    hostname = parsed.hostname
    if hostname is None:
        raise NonLocalInstanceError(f"Cannot determine hostname from URL: {base_url}")
    if is_loopback_host(hostname):
        return
    raise NonLocalInstanceError(
        f"Operation not allowed on remote instance: {hostname}. "
        f"Only localhost and loopback IPs are permitted."
    )


def warn_if_cleartext_secret(base_url: str) -> None:
    """Warn once per process when master password is sent over cleartext HTTP."""
    if _cleartext_warned[0]:
        return
    p = urlsplit(base_url)
    if p.scheme == "http" and (p.hostname is None or not is_loopback_host(p.hostname)):
        warnings.warn(
            "Sending master password over unencrypted HTTP to non-local instance — "
            "credentials will be transmitted in cleartext. Use HTTPS.",
            stacklevel=3,
        )
        _cleartext_warned[0] = True
