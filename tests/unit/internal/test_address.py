from __future__ import annotations

import socket
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.address import AddressState, normalize_bind_host, probe_address

if TYPE_CHECKING:
    import pytest


def test_normalizes_equivalent_local_addresses() -> None:
    assert normalize_bind_host("localhost") == "127.0.0.1"
    assert normalize_bind_host("0.0.0.0") == "0.0.0.0"
    assert normalize_bind_host("::") == "::"


def test_probe_returns_typed_state() -> None:
    assert probe_address("127.0.0.1", 0) is AddressState.FREE


def test_wildcard_probe_detects_specific_interface_listener() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    try:
        assert probe_address("0.0.0.0", listener.getsockname()[1]) is AddressState.OCCUPIED
    finally:
        listener.close()


def test_getaddrinfo_failure_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.address.socket.getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(socket.gaierror("no dns")),
    )
    assert probe_address("does-not-resolve.invalid", 8069) is AddressState.UNKNOWN
