"""Substitutes for the internal HTTP transport boundary in unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_instance_sdk.internal.transport import TransportStatusError

OPEN_ODOO_HTTP_CLIENT = "odoo_instance_sdk.internal.transport.factory.open_odoo_http_client"
HEALTH_OPEN_ODOO_HTTP_CLIENT = "odoo_instance_sdk.internal.health.open_odoo_http_client"


def make_response(
    *,
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
    content: bytes = b"",
    headers: dict[str, str] | None = None,
    is_error: bool | None = None,
    iter_bytes: list[bytes] | None = None,
) -> MagicMock:
    """Build a streaming response mock for the transport protocol."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.is_error = is_error if is_error is not None else status_code >= 400
    response.text = text
    response.content = content if content else text.encode()
    if json_data is not None:
        response.json.return_value = json_data
    if iter_bytes is not None:
        response.iter_bytes.return_value = iter(iter_bytes)
    if response.is_error:
        response.raise_for_status.side_effect = TransportStatusError(
            status_code=status_code,
            message=f"HTTP {status_code}",
            body=response.content,
        )
    else:
        response.raise_for_status.return_value = None
    return response


def make_http_client(
    *,
    post: Any = None,
    get: Any = None,
    post_side_effect: Any = None,
    get_side_effect: Any = None,
) -> MagicMock:
    """Build an HTTP client mock implementing the transport protocol."""
    http = MagicMock()
    if post_side_effect is not None:
        http.post.side_effect = post_side_effect
    elif post is not None:
        http.post.return_value = post
    if get_side_effect is not None:
        http.get.side_effect = get_side_effect
    elif get is not None:
        http.get.return_value = get
    http.close.return_value = None
    return http


def http_client_context(http: MagicMock) -> MagicMock:
    """Wrap an HTTP client mock in a context manager."""
    context = MagicMock()
    context.__enter__.return_value = http
    context.__exit__.return_value = None
    return context


def mock_http_for_json(json_data: object) -> MagicMock:
    """Return a context manager that yields a client returning ``json_data``."""
    response = make_response(json_data=json_data)
    http = make_http_client(post=response)

    def stream(*_args: object, **_kwargs: object) -> MagicMock:
        stream_context = MagicMock()
        stream_context.__enter__.return_value = response
        stream_context.__exit__.return_value = None
        return stream_context

    http.stream.side_effect = stream
    return http_client_context(http)


def stream_http(response: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Attach a streaming response to a client context manager."""
    http_context = mock_http_for_json({})
    http = http_context.__enter__.return_value
    stream_context = MagicMock()
    stream_context.__enter__.return_value = response
    stream_context.__exit__.return_value = None
    http.stream.side_effect = None
    http.stream.return_value = stream_context
    return http_context, http


@contextmanager
def patch_open_odoo_http_client(http: MagicMock) -> Iterator[MagicMock]:
    """Patch the internal factory and module-level imports to return ``http``."""
    context = http_client_context(http)
    with (
        patch(OPEN_ODOO_HTTP_CLIENT, return_value=context),
        patch(HEALTH_OPEN_ODOO_HTTP_CLIENT, return_value=context),
    ):
        yield http
