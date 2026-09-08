from __future__ import annotations

import socket
from typing import cast
from unittest.mock import Mock

import pytest

from odoo_instance_sdk.internal import address
from odoo_instance_sdk.internal.address import (
    AddressState,
    SocketAddress,
    _is_wildcard_sockaddr,
    _loopback_sockaddr,
    normalize_bind_host,
    probe_address,
)
from tests.cases.normalization import BIND_HOST_CASES


@pytest.mark.parametrize("raw, expected", BIND_HOST_CASES)
def test_normalizes_equivalent_local_addresses(raw: str, expected: str) -> None:
    assert normalize_bind_host(raw) == expected


def test_probe_returns_typed_state() -> None:
    assert probe_address("127.0.0.1", 0) is AddressState.FREE


def test_wildcard_probe_detects_specific_interface_listener() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    try:
        assert probe_address("0.0.0.0", listener.getsockname()[1]) is AddressState.OCCUPIED
    finally:
        listener.close()


def test_probe_allows_immediate_bind_after_closed_accepted_loopback() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(("127.0.0.1", port))
        accepted, _ = listener.accept()
        accepted.close()
        client.close()
        listener.close()
        assert probe_address("127.0.0.1", port) is AddressState.FREE
    finally:
        client.close()
        listener.close()


def test_probe_keeps_live_listener_occupied() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        assert probe_address("127.0.0.1", listener.getsockname()[1]) is AddressState.OCCUPIED
    finally:
        listener.close()


def test_socket_creation_failure_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise OSError("socket unavailable")

    monkeypatch.setattr("odoo_instance_sdk.internal.address.socket.socket", fail_socket)
    assert probe_address("127.0.0.1", 8069) is AddressState.UNKNOWN


def test_getaddrinfo_failure_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.address.socket.getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(socket.gaierror("no dns")),
    )
    assert probe_address("does-not-resolve.invalid", 8069) is AddressState.UNKNOWN


def test_probe_empty_resolution_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.address.socket.getaddrinfo",
        lambda *_args, **_kwargs: [],
    )
    assert probe_address("empty-resolution.test", 8069) is AddressState.UNKNOWN


def test_probe_wildcard_checks_loopback_after_free_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sockaddr = ("0.0.0.0", 8069)
    monkeypatch.setattr(
        "odoo_instance_sdk.internal.address.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", sockaddr)],
    )
    probe = Mock(side_effect=[AddressState.FREE, AddressState.OCCUPIED])
    monkeypatch.setattr(address, "_probe_bind", probe)

    assert probe_address("0.0.0.0", 8069) is AddressState.OCCUPIED
    assert probe.call_count == 2


@pytest.mark.parametrize(
    "sockaddr",
    [
        cast("SocketAddress", ()),
        cast("SocketAddress", (socket.AF_INET, b"")),
        cast("SocketAddress", ("not-an-address", 8069)),
    ],
)
def test_wildcard_and_loopback_helpers_reject_malformed_addresses(
    sockaddr: SocketAddress,
) -> None:
    assert _is_wildcard_sockaddr(sockaddr) is False
    assert _loopback_sockaddr(sockaddr) is None


def test_loopback_helper_supports_ipv4_and_ipv6_sockaddr_shapes() -> None:
    assert _loopback_sockaddr(("0.0.0.0", 8069)) == ("127.0.0.1", 8069)
    assert _loopback_sockaddr(("::", 8069)) == ("::1", 8069)
    assert _loopback_sockaddr(("::", 8069, 0, 0)) == ("::1", 8069, 0, 0)


def test_wildcard_helper_rejects_non_ip_text() -> None:
    assert _is_wildcard_sockaddr(("not-an-address", 8069)) is False
