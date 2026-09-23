"""Odoo HTTP client serving one Odoo origin.

:class:`OdooHttpClient` wraps :class:`httpx.Client` and serves all Odoo HTTP
endpoints for one origin (health/status, database list/create/drop/backup/
restore).  It converts ``httpx`` exceptions into :class:`TransportError`
subclasses so ``httpx`` never leaks to resource interfaces.  Resources catch
``TransportError`` and map to existing typed domain errors.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from odoo_instance_sdk.internal.transport.base import (
    _UNSET,
    BaseHttpClient,
    StreamingResponse,
)
from odoo_instance_sdk.internal.urls import warn_if_cleartext_secret


class _AdaptedStreamingResponse:
    """Wrap a raw ``httpx`` response and convert stream/status errors."""

    def __init__(self, raw: Any, client: OdooHttpClient, *, url: str) -> None:
        self._raw = raw
        self._client = client
        self._url = url

    @property
    def status_code(self) -> int:
        return cast("int", self._raw.status_code)

    @property
    def headers(self) -> Any:
        return self._raw.headers

    @property
    def is_error(self) -> bool:
        return cast("bool", self._raw.is_error)

    @property
    def text(self) -> str:
        return cast("str", self._raw.text)

    @property
    def content(self) -> bytes:
        return cast("bytes", self._raw.content)

    def json(self) -> Any:
        try:
            return self._raw.json()
        except BaseException as exc:
            self._reraise_transport(exc)
            raise AssertionError("unreachable transport conversion")

    def raise_for_status(self) -> None:
        try:
            self._raw.raise_for_status()
        except BaseException as exc:
            self._reraise_transport(exc)

    def iter_bytes(self, chunk_size: int = 8192) -> Iterator[bytes]:
        try:
            yield from self._raw.iter_bytes(chunk_size=chunk_size)
        except BaseException as exc:
            self._reraise_transport(exc)

    def _reraise_transport(self, exc: BaseException) -> None:
        import httpx

        if isinstance(exc, httpx.HTTPError):
            raise self._client._convert_exception(exc, url=self._url) from exc
        raise exc


class OdooHttpClient(BaseHttpClient):
    """HTTP client for one Odoo origin.

    Construct one per resource operation and close it on success/error/
    cancellation.  No process-wide shared singleton, no cross-origin cookie
    reuse, no retry.
    """

    def __enter__(self) -> OdooHttpClient:
        self._ensure_client()
        return self

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self._base_url = base_url

    @classmethod
    def for_origin(
        cls,
        base_url: str,
        *,
        timeout: float | None = None,
    ) -> OdooHttpClient:
        """Construct a client for one origin and warn on cleartext secrets."""
        warn_if_cleartext_secret(base_url)
        return cls(base_url=base_url, timeout=timeout)

    def post(
        self,
        url: str,
        *,
        json: Any = _UNSET,
        data: Any = _UNSET,
        files: Any = _UNSET,
    ) -> StreamingResponse:
        kwargs: dict[str, Any] = {}
        if json is not _UNSET:
            kwargs["json"] = json
        if data is not _UNSET:
            kwargs["data"] = data
        if files is not _UNSET:
            kwargs["files"] = files
        return self._request("POST", url, operation="post", perform=lambda c: c.post(url, **kwargs))

    def get(self, url: str) -> StreamingResponse:
        return self._request("GET", url, operation="get", perform=lambda c: c.get(url))

    @contextmanager
    def stream(
        self,
        method: str,
        url: str,
        *,
        data: Any = _UNSET,
    ) -> Iterator[StreamingResponse]:
        kwargs: dict[str, Any] = {}
        if data is not _UNSET:
            kwargs["data"] = data
        client = self._ensure_client()
        start = time.perf_counter()
        status: int | None = None
        try:
            with client.stream(method, url, **kwargs) as raw:
                status = raw.status_code
                yield _AdaptedStreamingResponse(raw, self, url=url)
        except BaseException as exc:
            import httpx

            if isinstance(exc, httpx.HTTPError):
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                converted = self._convert_exception(exc, url=url)
                self._log(
                    service="odoo",
                    operation="stream",
                    method=method,
                    url=url,
                    start=start,
                    status=status,
                    result_class="error",
                )
                raise converted from exc
            raise
        else:
            self._log(
                service="odoo",
                operation="stream",
                method=method,
                url=url,
                start=start,
                status=status,
                result_class="ok",
            )

    def _request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        perform: Any,
    ) -> StreamingResponse:
        """Run exactly one network attempt, log, and convert ``httpx`` errors."""
        client = self._ensure_client()
        start = time.perf_counter()
        status: int | None = None
        try:
            response = perform(client)
            status = response.status_code
            self._log(
                service="odoo",
                operation=operation,
                method=method,
                url=url,
                start=start,
                status=status,
                result_class="ok",
            )
            return _AdaptedStreamingResponse(response, self, url=url)
        except BaseException as exc:
            import httpx

            result_class = "error"
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
            if isinstance(exc, httpx.HTTPError):
                converted = self._convert_exception(exc, url=url)
                self._log(
                    service="odoo",
                    operation=operation,
                    method=method,
                    url=url,
                    start=start,
                    status=status,
                    result_class=result_class,
                )
                raise converted from exc
            self._log(
                service="odoo",
                operation=operation,
                method=method,
                url=url,
                start=start,
                status=status,
                result_class=result_class,
            )
            raise


__all__ = ["OdooHttpClient"]
