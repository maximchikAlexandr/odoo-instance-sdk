from __future__ import annotations

import importlib
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest

fastapi = pytest.importorskip("fastapi")
FastAPI = fastapi.FastAPI
TestClient = importlib.import_module("fastapi.testclient").TestClient
browser_login = importlib.import_module("odoo_instance_sdk.http.browser_login")
BrowserLoginController = browser_login.BrowserLoginController
build_browser_login_router = browser_login.build_browser_login_router
models = importlib.import_module("odoo_instance_sdk.models")
BrowserLoginTicketRequest = models.BrowserLoginTicketRequest
BrowserLoginUsersRequest = models.BrowserLoginUsersRequest

if TYPE_CHECKING:
    from odoo_instance_sdk.http.browser_login import LoginTarget
    from odoo_instance_sdk.models import BrowserLoginUser


class Resolver:
    def __init__(self) -> None:
        self.created_sessions: list[int] = []
        self.queries: list[str] = []
        self.failure: str | None = None
        self.target = cast(
            "LoginTarget",
            browser_login.LoginTarget(
                owner_kind="environment",
                owner_id="env-1",
                runtime_id="runtime-1",
                database="db-1",
                instance_url="https://instance.example.test",
                panel_origin="https://panel.example.test",
                list_users=self._list_users,
                create_session=self._create_session,
            ),
        )

    def resolve(self, owner_kind: str, owner_id: str) -> LoginTarget:
        if self.failure:
            raise RuntimeError(self.failure)
        assert (owner_kind, owner_id) == ("environment", "env-1")
        return self.target

    def _list_users(self, query: str) -> tuple[BrowserLoginUser, ...]:
        self.queries.append(query)
        return tuple(
            models.BrowserLoginUser(id=index, login=f"user{index}", name=f"User {index}")
            for index in range(1, 103)
            if not query or query in f"user{index}"
        )

    def _create_session(self, user_id: int) -> str:
        self.created_sessions.append(user_id)
        return f"private-sid-{user_id}"


def test_user_query_is_bounded_and_search_is_trimmed() -> None:
    resolver = Resolver()
    controller = BrowserLoginController(resolver)
    users = controller.users(
        BrowserLoginUsersRequest(owner_kind="environment", owner_id="env-1", query=" user1 ")
    )
    assert len(users) == 14
    assert users[0].login == "user1"
    controller.users(
        BrowserLoginUsersRequest(owner_kind="environment", owner_id="env-1", query="x" * 128)
    )
    assert resolver.queries[-1] == "x" * 128
    assert (
        len(controller.users(BrowserLoginUsersRequest(owner_kind="environment", owner_id="env-1")))
        == 100
    )


def test_ticket_is_one_time_bound_to_host_origin_and_runtime() -> None:
    resolver = Resolver()
    controller = BrowserLoginController(resolver)
    ticket = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=7),
        "https://panel.example.test",
    )
    assert "private-sid" not in repr(ticket)
    body = f"handoff={ticket.handoff}".encode()
    assert controller.consume(
        body,
        host="instance.example.test",
        origin="https://panel.example.test",
        trusted_proxy=True,
    ) == ("private-sid-7", "/odoo")
    retry = controller.consume(
        body,
        host="instance.example.test",
        origin="https://panel.example.test",
        trusted_proxy=True,
    )
    assert retry is None


@pytest.mark.parametrize(
    ("host", "origin", "trusted_proxy"),
    [
        ("neighbor.example.test", "https://panel.example.test", True),
        ("instance.example.test", "https://evil.example.test", True),
        ("instance.example.test", "https://panel.example.test", False),
    ],
)
def test_invalid_consume_boundary_burns_ticket(host: str, origin: str, trusted_proxy: bool) -> None:
    controller = BrowserLoginController(Resolver())
    ticket = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=7),
        "https://panel.example.test",
    )
    body = f"handoff={ticket.handoff}".encode()
    assert controller.consume(body, host=host, origin=origin, trusted_proxy=trusted_proxy) is None
    retry = controller.consume(
        body,
        host="instance.example.test",
        origin="https://panel.example.test",
        trusted_proxy=True,
    )
    assert retry is None


def test_expired_handoff_is_consumed(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(browser_login, "monotonic", lambda: now[0])
    controller = BrowserLoginController(Resolver())
    ticket = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=7),
        "https://panel.example.test",
    )
    now[0] = 131.0
    body = f"handoff={ticket.handoff}".encode()
    assert (
        controller.consume(
            body,
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )
    assert (
        controller.consume(
            f"handoff={ticket.handoff}".encode(),
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )


def test_handoff_capacity_rejects_and_expired_entries_are_pruned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(browser_login, "monotonic", lambda: now[0])
    controller = BrowserLoginController(Resolver())
    request = BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=7)
    tickets = [controller.issue(request, "https://panel.example.test") for _ in range(128)]

    with pytest.raises(ValueError, match="browser login is unavailable"):
        controller.issue(request, "https://panel.example.test")

    now[0] = 131.0
    assert (
        controller.consume(
            f"handoff={tickets[0].handoff}".encode(),
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )
    fresh = controller.issue(request, "https://panel.example.test")
    assert fresh.handoff not in {ticket.handoff for ticket in tickets}


def test_extra_form_field_rejects_and_burns_handoff() -> None:
    controller = BrowserLoginController(Resolver())
    ticket = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=7),
        "https://panel.example.test",
    )
    body = f"handoff={ticket.handoff}&extra=value".encode()
    assert (
        controller.consume(
            body,
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )
    assert (
        controller.consume(
            f"handoff={ticket.handoff}".encode(),
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )


def test_non_ascii_extra_field_rejects_and_burns_handoff() -> None:
    controller = BrowserLoginController(Resolver())
    ticket = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=7),
        "https://panel.example.test",
    )
    body = f"handoff={ticket.handoff}&extra=".encode() + b"\xff"
    assert (
        controller.consume(
            body,
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )
    assert (
        controller.consume(
            f"handoff={ticket.handoff}".encode(),
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )


def test_consume_revalidates_runtime_identity_before_session_creation() -> None:
    resolver = Resolver()
    controller = BrowserLoginController(resolver)
    ticket = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=7),
        "https://panel.example.test",
    )
    resolver.target = replace(resolver.target, database="changed-db")
    assert (
        controller.consume(
            f"handoff={ticket.handoff}".encode(),
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )
    assert resolver.created_sessions == []


def test_consume_revalidates_publication_host_before_session_creation() -> None:
    resolver = Resolver()
    controller = BrowserLoginController(resolver)
    ticket = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=7),
        "https://panel.example.test",
    )
    resolver.target = replace(resolver.target, instance_url="https://replaced.example.test")
    assert (
        controller.consume(
            f"handoff={ticket.handoff}".encode(),
            host="instance.example.test",
            origin="https://panel.example.test",
            trusted_proxy=True,
        )
        is None
    )
    assert resolver.created_sessions == []


def test_issue_errors_do_not_echo_private_resolver_errors() -> None:
    resolver = Resolver()
    resolver.failure = "private-sid=must-not-escape"
    app = FastAPI()
    app.include_router(
        build_browser_login_router(
            BrowserLoginController(resolver),
            external_origin=("https", ("panel.example.test",)),
            trusted_proxy_addresses=("testclient",),
        )
    )
    client = TestClient(app)
    client.cookies.set("odoo_instance_sdk_csrf", "csrf")
    headers = {
        "Origin": "https://panel.example.test",
        "X-CSRF-Token": "csrf",
        "Content-Type": "application/json",
    }
    response = client.post(
        "/api/v1/odoo/login-tickets",
        json={"owner_kind": "environment", "owner_id": "env-1", "user_id": 7},
        headers=headers,
    )
    assert response.status_code == 409
    assert "private-sid" not in response.text
    assert response.json()["message"] == "browser login unavailable"


def test_http_issue_and_consume_sets_only_secure_host_cookie() -> None:
    controller = BrowserLoginController(Resolver())
    app = FastAPI()
    app.include_router(
        build_browser_login_router(
            controller,
            external_origin=("https", ("panel.example.test",)),
            trusted_proxy_addresses=("testclient",),
        )
    )
    client = TestClient(app)
    client.cookies.set("odoo_instance_sdk_csrf", "csrf")
    headers = {
        "Origin": "https://panel.example.test",
        "X-CSRF-Token": "csrf",
        "Content-Type": "application/json",
    }
    issued = client.post(
        "/api/v1/odoo/login-tickets",
        json={"owner_kind": "environment", "owner_id": "env-1", "user_id": 42},
        headers=headers,
    )
    assert issued.status_code == 200
    assert issued.headers["cache-control"] == "no-store"
    consume_headers = {
        "Host": "instance.example.test",
        "Origin": "https://panel.example.test",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "instance.example.test",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    response = client.post(
        "/_odcli/auth/consume",
        data={"handoff": issued.json()["handoff"]},
        headers=consume_headers,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/odoo"
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "domain=" not in cookie
    replay = client.post(
        "/_odcli/auth/consume",
        data={"handoff": issued.json()["handoff"]},
        headers=consume_headers,
    )
    assert replay.status_code == 400


def test_get_and_head_do_not_consume_handoff_and_untrusted_form_does() -> None:
    controller = BrowserLoginController(Resolver())
    app = FastAPI()
    app.include_router(
        build_browser_login_router(
            controller,
            external_origin=("https", ("panel.example.test",)),
            trusted_proxy_addresses=("testclient",),
        )
    )
    client = TestClient(app)
    issued = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=42),
        "https://panel.example.test",
    )
    headers = {
        "Host": "instance.example.test",
        "Origin": "https://panel.example.test",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "instance.example.test",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    assert client.get(f"/_odcli/auth/consume?handoff={issued.handoff}").status_code == 405
    assert client.head("/_odcli/auth/consume").status_code == 405
    assert (
        client.post(
            "/_odcli/auth/consume",
            data={"handoff": issued.handoff},
            headers=headers,
            follow_redirects=False,
        ).status_code
        == 303
    )
    untrusted_ticket = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=43),
        "https://panel.example.test",
    )
    untrusted = dict(headers)
    untrusted["X-Forwarded-Proto"] = "http"
    assert (
        client.post(
            "/_odcli/auth/consume",
            data={"handoff": untrusted_ticket.handoff},
            headers=untrusted,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/_odcli/auth/consume",
            data={"handoff": untrusted_ticket.handoff},
            headers=headers,
        ).status_code
        == 400
    )


def test_wrong_content_type_burns_existing_handoff() -> None:
    controller = BrowserLoginController(Resolver())
    app = FastAPI()
    app.include_router(
        build_browser_login_router(
            controller,
            external_origin=("https", ("panel.example.test",)),
            trusted_proxy_addresses=("testclient",),
        )
    )
    client = TestClient(app)
    issued = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=42),
        "https://panel.example.test",
    )
    headers = {
        "Host": "instance.example.test",
        "Origin": "https://panel.example.test",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "instance.example.test",
        "Content-Type": "text/plain",
    }
    body = f"handoff={issued.handoff}"
    assert client.post("/_odcli/auth/consume", content=body, headers=headers).status_code == 400
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    assert client.post("/_odcli/auth/consume", content=body, headers=headers).status_code == 400


def test_oversized_consume_body_burns_handoff() -> None:
    controller = BrowserLoginController(Resolver())
    app = FastAPI()
    app.include_router(
        build_browser_login_router(
            controller,
            external_origin=("https", ("panel.example.test",)),
            trusted_proxy_addresses=("testclient",),
        )
    )
    client = TestClient(app)
    issued = controller.issue(
        BrowserLoginTicketRequest(owner_kind="environment", owner_id="env-1", user_id=42),
        "https://panel.example.test",
    )
    headers = {
        "Host": "instance.example.test",
        "Origin": "https://panel.example.test",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "instance.example.test",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    assert (
        client.post(
            "/_odcli/auth/consume",
            content=f"handoff={issued.handoff}&extra={'x' * 600}",
            headers=headers,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/_odcli/auth/consume", data={"handoff": issued.handoff}, headers=headers
        ).status_code
        == 400
    )
