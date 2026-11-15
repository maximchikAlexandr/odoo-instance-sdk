"""Loopback unlock for the pgAdmin desktop master password."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from http.client import HTTPMessage
from http.cookiejar import CookieJar
from typing import IO
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import PgAdminUnavailableError

_CSRF_PATTERNS = (
    re.compile(rb"name=\"csrf-token\"\s+content=\"([^\"]+)\"", re.IGNORECASE),
    re.compile(rb"name='csrf-token'\s+content='([^']+)'", re.IGNORECASE),
    re.compile(rb"csrfToken\s*[:=]\s*\"([^\"]+)\""),
)


class _LoopbackRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect that leaves the addressed loopback origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        origin = urlsplit(req.full_url)
        target = urlsplit(newurl)
        if target.scheme != "http" or target.hostname != "127.0.0.1" or target.port != origin.port:
            raise PgAdminUnavailableError()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def unlock_master_password(port: int, password: str, *, deadline: float) -> None:
    """Submit a stored master password to the local pgAdmin desktop route.

    Args:
        port: Loopback port published by the SDK-owned container.
        password: Master password read from the private env file.
        deadline: Monotonic time at which the attempt must stop.

    Raises:
        PgAdminUnavailableError: pgAdmin did not accept the password.
    """
    if not isinstance(port, int) or not password:
        raise PgAdminUnavailableError()
    root = f"http://127.0.0.1:{port}/"
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        _LoopbackRedirect(),
    )
    page = _read(opener, urllib.request.Request(root), deadline)
    request = urllib.request.Request(
        f"{root}browser/master_password",
        data=json.dumps({"password": password, "submit_password": True}).encode(),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    token = _csrf_token(page)
    if token is not None:
        request.add_header("X-pgA-CSRFToken", token)
    _require_present(_read(opener, request, deadline))


def _read(
    opener: urllib.request.OpenerDirector, request: urllib.request.Request, deadline: float
) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PgAdminUnavailableError()
    try:
        with opener.open(request, timeout=remaining) as response:
            final_url = response.geturl()
            body = response.read(64 * 1024)
    except (OSError, urllib.error.URLError, TimeoutError):
        raise PgAdminUnavailableError() from None
    origin = urlsplit(request.full_url)
    final = urlsplit(final_url)
    if (
        final.scheme != "http"
        or final.hostname != "127.0.0.1"
        or final.port != origin.port
        or not isinstance(body, bytes)
    ):
        raise PgAdminUnavailableError()
    return body


def _csrf_token(page: bytes) -> str | None:
    for pattern in _CSRF_PATTERNS:
        match = pattern.search(page)
        if match is not None:
            return match.group(1).decode("ascii", errors="ignore")
    return None


def _require_present(payload: bytes) -> None:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        raise PgAdminUnavailableError() from None
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, dict):
        raise PgAdminUnavailableError()
    errmsg = data.get("errmsg")
    if data.get("present") is not True or data.get("is_error") is True or errmsg:
        raise PgAdminUnavailableError()
