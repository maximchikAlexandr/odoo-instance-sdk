from __future__ import annotations

import enum
import socket


class AddressState(enum.StrEnum):
    FREE = "free"
    OCCUPIED = "occupied"
    UNKNOWN = "unknown"


def normalize_bind_host(host: str) -> str:
    """Canonical local bind target without changing wildcard semantics."""
    normalized = host.strip().lower()
    if normalized in ("", "localhost"):
        return "127.0.0.1"
    return normalized


def probe_address(host: str, port: int) -> AddressState:
    target = normalize_bind_host(host)
    try:
        infos = socket.getaddrinfo(target, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return AddressState.UNKNOWN
    if not infos:
        return AddressState.UNKNOWN
    for family, socktype, proto, _canonname, sockaddr in infos:
        try:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(0.2)
            try:
                sock.bind(sockaddr)
            except OSError:
                return AddressState.OCCUPIED
            finally:
                sock.close()
        except OSError:
            return AddressState.UNKNOWN
    return AddressState.FREE
