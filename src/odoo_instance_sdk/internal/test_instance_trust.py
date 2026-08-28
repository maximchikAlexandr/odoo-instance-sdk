"""User-owned approvals for repository-selected test-instance origins."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import ConfigError
from odoo_instance_sdk.internal.urls import (
    assert_password_transport_allowed,
    canonical_origin,
    is_loopback_host,
)

_PIN_ENV = "ODCLI_TEST_INSTANCE_ORIGIN_PINS"


def _canonicalize(value: str) -> str:
    try:
        return canonical_origin(value)
    except Exception:
        return value.strip()


def approved_test_instance_origins(*, environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Read comma-separated exact origin pins from the operator environment."""
    values = os.environ if environ is None else environ
    return frozenset(
        _canonicalize(item) for item in values.get(_PIN_ENV, "").split(",") if item.strip()
    )


def require_test_instance_origin_approval(base_url: str) -> str:
    """Validate transport and require an exact user-owned origin approval."""
    assert_password_transport_allowed(base_url)
    origin = canonical_origin(base_url)
    # Loopback is a local operator-controlled endpoint; remote origins require
    # a pin that repository configuration cannot create or modify.
    if is_loopback_host(urlsplit(base_url).hostname or ""):
        return origin
    if origin not in approved_test_instance_origins():
        raise ConfigError(f"test-instance origin {origin!r} is not approved outside the repository")
    return origin


__all__ = ["approved_test_instance_origins", "require_test_instance_origin_approval"]
