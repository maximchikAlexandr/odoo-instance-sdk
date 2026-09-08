from __future__ import annotations

import enum
import errno
import ipaddress
import socket


class AddressState(enum.StrEnum):
    FREE = "free"
    OCCUPIED = "occupied"
    UNKNOWN = "unknown"


SocketAddress = tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes]


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
        state = _probe_bind(family, socktype, proto, sockaddr)
        if state is not AddressState.FREE:
            return state
        if _is_wildcard_sockaddr(sockaddr):
            loopback = _loopback_sockaddr(sockaddr)
            if loopback is not None:
                state = _probe_bind(family, socktype, proto, loopback)
                if state is not AddressState.FREE:
                    return state
    return AddressState.FREE


def _probe_bind(family: int, socktype: int, proto: int, sockaddr: SocketAddress) -> AddressState:
    try:
        sock = socket.socket(family, socktype, proto)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.2)
        try:
            sock.bind(sockaddr)
        except OSError as exc:
            return AddressState.OCCUPIED if exc.errno == errno.EADDRINUSE else AddressState.UNKNOWN
        finally:
            sock.close()
    except OSError:
        return AddressState.UNKNOWN
    return AddressState.FREE


def _is_wildcard_sockaddr(sockaddr: SocketAddress) -> bool:
    if not sockaddr or not isinstance(sockaddr[0], str):
        return False
    try:
        return ipaddress.ip_address(str(sockaddr[0])).is_unspecified
    except ValueError:
        return False


def _loopback_sockaddr(sockaddr: SocketAddress) -> SocketAddress | None:
    if len(sockaddr) < 2 or not isinstance(sockaddr[0], str):
        return None
    try:
        address = ipaddress.ip_address(str(sockaddr[0]))
    except ValueError:
        return None
    if address.version == 4:
        return ("127.0.0.1", sockaddr[1])
    if len(sockaddr) >= 4:
        return ("::1", sockaddr[1], sockaddr[2], sockaddr[3])
    return ("::1", sockaddr[1])
