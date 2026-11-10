from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, cast

import pytest

fastapi = pytest.importorskip("fastapi")
FastAPI = fastapi.FastAPI
TestClient = importlib.import_module("fastapi.testclient").TestClient
browser_login = importlib.import_module("odoo_instance_sdk.http.browser_login")
BrowserLoginController = browser_login.BrowserLoginController
build_browser_login_router = browser_login.build_browser_login_router

if TYPE_CHECKING:
    from odoo_instance_sdk.http.browser_login import LoginTarget


def test_selected_user_handoff_sets_host_cookie_once() -> None:
    class Resolver:
        def resolve(self, owner_kind: str, owner_id: str) -> LoginTarget:
            assert (owner_kind, owner_id) == ("project", "project-fixture")
            return cast(
                "LoginTarget",
                browser_login.LoginTarget(
                    owner_kind="project",
                    owner_id=owner_id,
                    runtime_id="ready-runtime",
                    database="fixture-db",
                    instance_url="https://instance.example.test",
                    panel_origin="https://panel.example.test",
                    list_users=lambda _query: (),
                    create_session=lambda user_id: f"sid-for-user-{user_id}",
                ),
            )

    app = FastAPI()
    app.include_router(
        build_browser_login_router(
            BrowserLoginController(Resolver()),
            external_origin=("https", ("panel.example.test",)),
            trusted_proxy_addresses=("testclient",),
        )
    )
    client = TestClient(app)
    client.cookies.set("odoo_instance_sdk_csrf", "csrf")
    panel_headers = {
        "Origin": "https://panel.example.test",
        "X-CSRF-Token": "csrf",
        "Content-Type": "application/json",
    }
    issued = client.post(
        "/api/v1/odoo/login-tickets",
        json={"owner_kind": "project", "owner_id": "project-fixture", "user_id": 73},
        headers=panel_headers,
    )
    assert issued.status_code == 200
    assert issued.headers["cache-control"] == "no-store"
    handoff = issued.json()["handoff"]
    headers = {
        "Host": "instance.example.test",
        "Origin": "https://panel.example.test",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "instance.example.test",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    response = client.post(
        "/_odcli/auth/consume",
        data={"handoff": handoff},
        headers=headers,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/odoo"
    cookie = response.headers["set-cookie"].lower()
    assert "session_id=sid-for-user-73" in cookie
    assert all(flag in cookie for flag in ("secure", "httponly", "samesite=lax"))
    assert "domain=" not in cookie
    assert (
        client.post("/_odcli/auth/consume", data={"handoff": handoff}, headers=headers).status_code
        == 400
    )
