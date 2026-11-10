from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from odoo_instance_sdk.http.browser_login import (
    BrowserLoginController,
    LoginTarget,
    build_browser_login_router,
)
from odoo_instance_sdk.models import (
    BrowserLoginTicketRequest,
    BrowserLoginUser,
    BrowserLoginUsersRequest,
)


class Resolver:
    def __init__(self) -> None:
        self.target = LoginTarget(
            owner_kind="environment",
            owner_id="env-1",
            runtime_id="runtime-1",
            database="db-1",
            instance_url="https://instance.example.test",
            panel_origin="https://panel.example.test",
            list_users=lambda query: tuple(
                BrowserLoginUser(id=index, login=f"user{index}", name=f"User {index}")
                for index in range(1, 103)
                if not query or query in f"user{index}"
            ),
            create_session=lambda user_id: f"private-sid-{user_id}",
        )

    def resolve(self, owner_kind: str, owner_id: str) -> LoginTarget:
        assert (owner_kind, owner_id) == ("environment", "env-1")
        return self.target


def test_user_query_is_bounded_and_search_is_trimmed() -> None:
    controller = BrowserLoginController(Resolver())
    users = controller.users(
        BrowserLoginUsersRequest(owner_kind="environment", owner_id="env-1", query=" user1 ")
    )
    assert len(users) == 14
    assert users[0].login == "user1"
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
    assert retry == ("private-sid-7", "/odoo") if not trusted_proxy else retry is None


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
