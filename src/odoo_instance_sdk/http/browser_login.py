from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import parse_qsl, urlsplit

import msgspec

from odoo_instance_sdk.models import (
    BrowserLoginTicket,
    BrowserLoginTicketRequest,
    BrowserLoginUser,
    BrowserLoginUsersRequest,
    BrowserLoginUsersResponse,
    HttpError,
    HttpErrorCode,
)

if TYPE_CHECKING:
    from fastapi import APIRouter


@dataclass(frozen=True, slots=True)
class LoginTarget:
    owner_kind: Literal["project", "environment"]
    owner_id: str
    runtime_id: str
    database: str
    instance_url: str
    panel_origin: str
    list_users: Callable[[str], tuple[BrowserLoginUser, ...]]
    create_session: Callable[[int], str]


class LoginTargetResolver(Protocol):
    def resolve(
        self, owner_kind: Literal["project", "environment"], owner_id: str
    ) -> LoginTarget: ...


@dataclass(frozen=True, slots=True)
class _Ticket:
    target: LoginTarget
    user_id: int
    expires: float


class BrowserLoginController:
    """Small process-local, atomically consumed instance-host handoffs."""

    def __init__(self, resolver: LoginTargetResolver) -> None:
        self._resolver = resolver
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def users(self, request: BrowserLoginUsersRequest) -> tuple[BrowserLoginUser, ...]:
        target = self._resolver.resolve(request.owner_kind, request.owner_id)
        return target.list_users(request.query.strip()[:80])[:100]

    def issue(self, request: BrowserLoginTicketRequest, panel_origin: str) -> BrowserLoginTicket:
        target = self._resolver.resolve(request.owner_kind, request.owner_id)
        if not target.instance_url.startswith("https://") or not panel_origin.startswith(
            "https://"
        ):
            raise ValueError("browser login is unavailable")
        token = secrets.token_urlsafe(32)
        deadline = monotonic() + 30
        with self._lock:
            now = monotonic()
            self._tickets = {
                key: value for key, value in self._tickets.items() if value.expires > now
            }
            self._tickets[token] = _Ticket(target, request.user_id, deadline)
        return BrowserLoginTicket(
            action_url=f"{target.instance_url.rstrip('/')}/_odcli/auth/consume",
            method="POST",
            handoff=token,
            expires_at=(datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        )

    def consume(
        self, body: bytes, *, host: str, origin: str, trusted_proxy: bool
    ) -> tuple[str, str] | None:
        if not trusted_proxy or len(body) > 512:
            return None
        try:
            fields = parse_qsl(body.decode("ascii"), keep_blank_values=True, strict_parsing=True)
        except (UnicodeDecodeError, ValueError):
            return None
        if len(fields) != 1 or fields[0][0] != "handoff" or not fields[0][1]:
            return None
        with self._lock:
            ticket = self._tickets.pop(fields[0][1], None)
        if ticket is None or ticket.expires <= monotonic():
            return None
        target = ticket.target
        expected = urlsplit(target.instance_url).netloc.lower()
        current_host = host.strip().lower()
        if current_host != expected or origin != target.panel_origin:
            return None
        try:
            current = self._resolver.resolve(target.owner_kind, target.owner_id)
            if (
                current.runtime_id,
                current.database,
                current.instance_url,
                current.panel_origin,
            ) != (target.runtime_id, target.database, target.instance_url, target.panel_origin):
                return None
            sid = current.create_session(ticket.user_id)
        except Exception:
            return None
        return sid, "/odoo"


def build_browser_login_router(  # noqa: C901
    controller: BrowserLoginController,
    *,
    external_origin: tuple[str, tuple[str, ...]],
    trusted_proxy_addresses: tuple[str, ...],
) -> APIRouter:
    from fastapi import APIRouter, Request
    from fastapi.responses import Response

    from odoo_instance_sdk.http.monitor import request_is_safe

    router = APIRouter()
    globals()["Request"] = Request
    globals()["Response"] = Response

    def error(status: int = 409) -> Response:
        return Response(
            content=msgspec.json.encode(
                HttpError(
                    code=HttpErrorCode.browser_login_unavailable,
                    message="browser login unavailable",
                )
            ),
            status_code=status,
            media_type="application/json",
        )

    async def users(request: Request) -> Response:
        if not request_is_safe(request, external_origin=external_origin):
            return error(422)
        try:
            payload = msgspec.json.decode(
                await request.body(), type=BrowserLoginUsersRequest, strict=True
            )
            result = controller.users(payload)
        except Exception:
            return error()
        return Response(
            content=msgspec.json.encode(BrowserLoginUsersResponse(users=result)),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    async def issue(request: Request) -> Response:
        if not request_is_safe(request, external_origin=external_origin):
            return error(422)
        try:
            payload = msgspec.json.decode(
                await request.body(), type=BrowserLoginTicketRequest, strict=True
            )
            ticket = controller.issue(payload, request.headers["origin"])
        except Exception:
            return error()
        return Response(
            content=msgspec.json.encode(ticket),
            media_type="application/json",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    async def consume(request: Request) -> Response:
        peer = request.client.host if request.client else ""
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/x-www-form-urlencoded"
        ):
            return Response(
                "Browser login unavailable", status_code=400, headers={"Cache-Control": "no-store"}
            )
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 512:
                return Response(
                    "Browser login unavailable",
                    status_code=400,
                    headers={"Cache-Control": "no-store"},
                )
            body.extend(chunk)
        result = controller.consume(
            bytes(body),
            host=request.headers.get("host", ""),
            origin=request.headers.get("origin", ""),
            trusted_proxy=peer in trusted_proxy_addresses
            and request.headers.get("x-forwarded-proto", "").lower() == "https"
            and request.headers.get("x-forwarded-host", "").lower()
            == request.headers.get("host", "").lower(),
        )
        if result is None:
            return Response(
                "Browser login unavailable", status_code=400, headers={"Cache-Control": "no-store"}
            )
        sid, destination = result
        response = Response(
            status_code=303, headers={"Location": destination, "Cache-Control": "no-store"}
        )
        response.set_cookie("session_id", sid, path="/", secure=True, httponly=True, samesite="lax")
        return response

    router.post("/api/v1/odoo/users/query", operation_id="queryOdooBrowserLoginUsers")(users)
    router.post("/api/v1/odoo/login-tickets", operation_id="issueOdooBrowserLoginTicket")(issue)
    router.add_api_route("/_odcli/auth/consume", consume, methods=["POST"], include_in_schema=False)
    return router
