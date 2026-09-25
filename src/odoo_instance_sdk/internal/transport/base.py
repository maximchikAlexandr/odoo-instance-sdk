"""Narrow internal HTTP transport over ``httpx``.

This is the only directory in ``src/`` that imports ``httpx`` directly.
Resources depend on the typed protocols defined here and on
:class:`OdooHttpClient`; they never reference ``httpx`` types or exceptions.
``httpx`` is imported lazily so that ``import odoo_instance_sdk.cli`` stays
free of it (startup-evidence gate).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, BinaryIO, Protocol, cast, runtime_checkable

from odoo_instance_sdk.execution import JsonValue

if TYPE_CHECKING:
    from types import TracebackType

_LOG = logging.getLogger("odoo_instance_sdk.transport")


class _Unset:
    __slots__ = ()


_UNSET = _Unset()

FileUpload = tuple[str, BinaryIO, str]
HttpPayload = (
    JsonValue | bytes | Mapping[str, str] | Mapping[str, FileUpload] | Sequence[tuple[str, str]]
)
RequestPayload = HttpPayload | _Unset


class _RawResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    is_error: bool
    text: str
    content: bytes

    def json(self) -> JsonValue: ...

    def raise_for_status(self) -> None: ...

    def iter_bytes(self, chunk_size: int = 8192) -> Iterator[bytes]: ...


class _RawHttpClient(Protocol):
    def post(self, url: str, **kwargs: RequestPayload) -> _RawResponse: ...

    def get(self, url: str) -> _RawResponse: ...

    def stream(
        self, method: str, url: str, **kwargs: RequestPayload
    ) -> AbstractContextManager[_RawResponse]: ...

    def close(self) -> None: ...


class TransportError(Exception):
    """Internal transport error base; never leaks to public callers."""


class TransportUnavailableError(TransportError):
    """Connect/read/write/timeout failure with no HTTP response."""


class TransportStatusError(TransportError):
    """An HTTP response was received but carried a non-success status."""

    def __init__(self, status_code: int, message: str, body: bytes) -> None:
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(message)


class TransportProtocolError(TransportError):
    """The response payload could not be decoded as expected."""


@runtime_checkable
class StreamingResponse(Protocol):
    """Read-only view of an HTTP response used for streaming downloads."""

    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def is_error(self) -> bool: ...

    @property
    def text(self) -> str: ...

    @property
    def content(self) -> bytes: ...

    def json(self) -> JsonValue: ...

    def raise_for_status(self) -> None: ...

    def iter_bytes(self, chunk_size: int = 8192) -> Iterator[bytes]: ...


@runtime_checkable
class HttpClient(Protocol):
    """Concrete DI seam for the transport boundary.

    Resources and tests depend on this protocol, not on ``httpx``.
    """

    def post(
        self,
        url: str,
        *,
        json: RequestPayload = _UNSET,
        data: RequestPayload = _UNSET,
        files: RequestPayload = _UNSET,
    ) -> StreamingResponse: ...

    def get(self, url: str) -> StreamingResponse: ...

    def stream(
        self,
        method: str,
        url: str,
        *,
        data: RequestPayload = _UNSET,
    ) -> AbstractContextManager[StreamingResponse]: ...

    def close(self) -> None: ...


def _safe_origin(url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    host = parsed.hostname or "unknown"
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    port_suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme}://{host}{port_suffix}"


def _safe_path(url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    return parsed.path or "/"


def _log_request(
    *,
    service: str,
    operation: str,
    method: str,
    url: str,
    elapsed: float,
    status: int | None,
    result_class: str,
) -> None:
    """Structured redacted request log.  No body/password/cookie/token values."""
    _LOG.debug(
        "transport.request service=%s operation=%s method=%s origin=%s path=%s "
        "status=%s elapsed_ms=%.1f result=%s",
        service,
        operation,
        method,
        _safe_origin(url),
        _safe_path(url),
        status if status is not None else "-",
        elapsed * 1000,
        result_class,
    )


class BaseHttpClient(AbstractContextManager["BaseHttpClient"]):
    """Minimal base HTTP client wrapping ``httpx.Client``.

    Owns common transport mechanics: managed ``httpx.Client`` with connection
    pooling, context-manager/``close()`` lifecycle, structured redacted logging,
    and conversion of ``httpx`` exceptions into :class:`TransportError`
    subclasses without leaking ``httpx`` types.  No retry, no cross-origin
    cookie/auth reuse.  Subclasses own service-specific payload/error parsing.
    """

    def __init__(self, *, timeout: float | None = None, trust_env: bool = True) -> None:
        self._timeout = timeout
        self._trust_env = trust_env
        self._client: _RawHttpClient | None = None

    def _ensure_client(self) -> _RawHttpClient:
        if self._client is None:
            import httpx

            timeout = self._timeout if self._timeout is not None else 30.0
            self._client = cast(
                "_RawHttpClient",
                httpx.Client(timeout=httpx.Timeout(timeout), trust_env=self._trust_env),
            )
        return self._client

    def __enter__(self) -> BaseHttpClient:
        self._ensure_client()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        import contextlib

        if self._client is not None:
            with contextlib.suppress(OSError):
                self._client.close()
            self._client = None

    def _convert_exception(self, exc: BaseException, *, url: str) -> TransportError:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            return TransportStatusError(
                status_code=response.status_code,
                message=f"HTTP {response.status_code} from {_safe_origin(url)}",
                body=b"",
            )
        return TransportUnavailableError(
            f"Transport request to {_safe_origin(url)} failed: {type(exc).__name__}"
        )

    def _log(
        self,
        *,
        service: str,
        operation: str,
        method: str,
        url: str,
        start: float,
        status: int | None,
        result_class: str,
    ) -> None:
        _log_request(
            service=service,
            operation=operation,
            method=method,
            url=url,
            elapsed=time.perf_counter() - start,
            status=status,
            result_class=result_class,
        )


__all__ = [
    "BaseHttpClient",
    "HttpClient",
    "StreamingResponse",
    "TransportError",
    "TransportProtocolError",
    "TransportStatusError",
    "TransportUnavailableError",
]
