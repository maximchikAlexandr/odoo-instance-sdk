"""Private pgAdmin HTTP readiness probing."""

from __future__ import annotations

import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from odoo_instance_sdk.exceptions import PgAdminUnavailableError


class _HttpResponse(Protocol):
    status: int
    code: int

    def geturl(self) -> str: ...
    def read(self, size: int = -1) -> bytes: ...


_READINESS_RETRY_INTERVAL = 0.25


def _validate_response(response: _HttpResponse, root_url: str) -> None:
    final_url = getattr(response, "geturl", lambda: root_url)()
    if final_url != root_url:
        raise PgAdminUnavailableError()
    status = getattr(response, "status", None)
    if not isinstance(status, int):
        status = getattr(response, "code", 200)
    if status != 200:
        raise PgAdminUnavailableError()
    body = response.read(64 * 1024)
    if isinstance(body, bytes):
        page = body.decode("utf-8", errors="ignore").lower()
    elif isinstance(body, str):
        page = body.lower()
    else:
        raise PgAdminUnavailableError()
    password_input = any(
        marker in page
        for marker in (
            'type="password"',
            "type='password'",
            'name="password"',
            "name='password'",
        )
    )
    if password_input or ("<form" in page and ("login" in page or "sign in" in page)):
        raise PgAdminUnavailableError()


def _probe(root_url: str, *, timeout: float) -> None:
    with urlopen(root_url, timeout=timeout) as response:
        _validate_response(response, root_url)


def wait_ready(port: int, *, deadline: float) -> None:
    root_url = f"http://127.0.0.1:{port}/"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PgAdminUnavailableError()
        try:
            _probe(root_url, timeout=remaining)
        except PgAdminUnavailableError:
            raise
        except HTTPError:
            raise PgAdminUnavailableError() from None
        except (OSError, URLError, TimeoutError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PgAdminUnavailableError() from None
            time.sleep(min(_READINESS_RETRY_INTERVAL, remaining))
        else:
            return
