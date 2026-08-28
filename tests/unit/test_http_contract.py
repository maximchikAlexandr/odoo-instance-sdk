from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import pytest

from odoo_instance_sdk.exceptions import (
    MonitorError,
    PgAdminDatabaseNotFoundError,
    PgAdminEnvironmentNotFoundError,
    PgAdminNotEligibleError,
    PgAdminUnavailableError,
)
from odoo_instance_sdk.models import (
    HttpError,
    HttpErrorCode,
    PgAdminOpenResult,
    PgAdminOpenState,
    Snapshot,
)


def _validate_json(  # noqa: C901
    value: Any, schema: dict[str, Any], components: dict[str, Any]
) -> None:
    if "$ref" in schema:
        reference = schema["$ref"]
        assert reference.startswith("#/components/schemas/")
        name = reference.removeprefix("#/components/schemas/")
        assert name in components
        _validate_json(value, components[name], components)
        return
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _validate_json(value, option, components)
            except AssertionError:
                continue
            return
        raise AssertionError(f"value does not match anyOf: {value!r}")
    if "enum" in schema:
        assert value in schema["enum"]
    schema_type = schema.get("type")
    if schema_type == "null":
        assert value is None
    elif schema_type == "string":
        assert isinstance(value, str)
    elif schema_type == "integer":
        assert isinstance(value, int) and not isinstance(value, bool)
    elif schema_type == "number":
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
    elif schema_type == "boolean":
        assert isinstance(value, bool)
    elif schema_type == "array":
        assert isinstance(value, list)
        for item in value:
            _validate_json(item, schema["items"], components)
    elif schema_type == "object":
        assert isinstance(value, dict)
        for required in schema.get("required", ()):
            assert required in value
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(schema.get("properties", {}))
        for name, property_schema in schema.get("properties", {}).items():
            if name in value:
                _validate_json(value[name], property_schema, components)


def _schema_monitor(snapshot: Snapshot) -> Any:
    class Monitor:
        def __init__(self) -> None:
            self.project_ids: list[str | None] = []

        def snapshot(self, project_id: str | None = None) -> Snapshot:
            self.project_ids.append(project_id)
            return snapshot

    return Monitor()


def _safe_pgadmin_headers(client: Any) -> dict[str, str]:
    """Bootstrap the same-origin session token used by the browser client."""
    response = client.get("/healthz")
    assert response.status_code == 200
    token = client.cookies.get("odoo_instance_sdk_csrf")
    assert isinstance(token, str) and token
    return {
        "Content-Type": "application/json",
        "Origin": "http://localhost",
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": token,
    }


def test_core_imports_keep_dashboard_dependencies_lazy() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import odoo_instance_sdk; import odoo_instance_sdk.cli; "
                "import odoo_instance_sdk.internal.serve; import odoo_instance_sdk.http.app; "
                "assert not {name for name in ('fastapi', 'starlette', 'uvicorn') if name in sys.modules}"
            ),
        ],
        check=True,
    )


@pytest.mark.dashboard
def test_openapi_uses_stable_msgspec_components_and_resolvable_refs() -> None:
    from odoo_instance_sdk.http.app import create_app

    snapshot = Snapshot(
        schema_version=3, generated_at=datetime.now(UTC), projects=(), environments=()
    )
    first = create_app(headless=True, monitor=_schema_monitor(snapshot)).openapi()
    second = create_app(headless=True, monitor=_schema_monitor(snapshot)).openapi()

    assert first == second
    components = first["components"]["schemas"]
    operation = first["paths"]["/api/v1/snapshot"]["get"]
    assert operation["operationId"] == "getMonitorSnapshot"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Snapshot"
    }
    assert operation["responses"]["500"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HttpError"
    }
    assert components["EnvironmentSnapshot"]["properties"]["pgadmin"] == {
        "$ref": "#/components/schemas/PgAdminEligibility"
    }
    assert {"type": "null"} in components["ProjectSummary"]["properties"]["cluster"]["anyOf"]
    assert components["PgAdminEligibilityState"]["enum"] == [
        "cluster_not_owned",
        "cluster_unhealthy",
        "database_unresolved",
        "eligible",
        "environment_not_ready",
    ]

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].removeprefix("#/components/schemas/") in components
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(first)


@pytest.mark.dashboard
def test_snapshot_bytes_match_production_openapi_schema_and_are_not_cli_enveloped() -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    snapshot = Snapshot(
        schema_version=3,
        generated_at=datetime(2026, 8, 27, tzinfo=UTC),
        projects=(),
        environments=(),
    )
    monitor = _schema_monitor(snapshot)
    app = create_app(headless=True, monitor=monitor)
    response = TestClient(app, base_url="http://localhost").get(
        "/api/v1/snapshot?project_id=project_x"
    )

    assert response.status_code == 200
    payload = json.loads(response.content)
    schema = app.openapi()["components"]["schemas"]["Snapshot"]
    _validate_json(payload, schema, app.openapi()["components"]["schemas"])
    assert set(payload) == {"schema_version", "generated_at", "projects", "environments"}
    assert monitor.project_ids == ["project_x"]


@pytest.mark.dashboard
def test_sanitized_error_bytes_match_production_openapi_schema() -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    class FailingMonitor:
        def snapshot(self, project_id: str | None = None) -> object:
            raise MonitorError("secret /absolute/internal/path")

    app = create_app(headless=True, monitor=FailingMonitor())
    response = TestClient(app, base_url="http://localhost").get("/api/v1/snapshot")

    assert response.status_code == 500
    payload = json.loads(response.content)
    components = app.openapi()["components"]["schemas"]
    _validate_json(payload, components["HttpError"], components)
    assert payload == {"code": "monitor_snapshot_failed", "message": "monitor snapshot failed"}
    assert "secret" not in response.text
    assert "/absolute" not in response.text
    assert msgspec.json.decode(response.content, type=HttpError) == HttpError(
        code=HttpErrorCode.monitor_snapshot_failed, message="monitor snapshot failed"
    )


@pytest.mark.dashboard
def test_unexpected_monitor_error_is_typed_json_and_matches_openapi() -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    class FailingMonitor:
        def snapshot(self, project_id: str | None = None) -> object:
            raise RuntimeError("password=top-secret /Users/private/catalog.sqlite3")

    app = create_app(headless=True, monitor=FailingMonitor())
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/api/v1/snapshot")
    payload = json.loads(response.content)
    components = app.openapi()["components"]["schemas"]
    _validate_json(payload, components["HttpError"], components)
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert payload == {"code": "monitor_snapshot_failed", "message": "monitor snapshot failed"}
    assert "top-secret" not in response.text
    assert "/Users" not in response.text


@pytest.mark.dashboard
def test_static_free_ui_composition_registers_pgadmin_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.http import app as app_module
    from odoo_instance_sdk.http.app import create_app

    class Monitor:
        def __init__(self) -> None:
            self.calls = 0

        def snapshot(self, project_id: str | None = None) -> Snapshot:
            self.calls += 1
            return Snapshot(
                schema_version=3, generated_at=datetime.now(UTC), projects=(), environments=()
            )

    monitor = Monitor()
    opener_calls: list[str] = []

    def opener(environment_id: str) -> PgAdminOpenResult:
        opener_calls.append(environment_id)
        return PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://127.0.0.1:5050")

    monkeypatch.setattr(app_module, "_WEB_DIST", Path("/missing/dashboard-dist"))
    app = create_app(
        headless=False,
        static_assets=False,
        monitor=monitor,
        pgadmin_opener=opener,
    )
    assert monitor.calls == 0
    assert opener_calls == []
    assert "/api/v1/pgadmin/open" in app.openapi()["paths"]

    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/v1/pgadmin/open",
            content=b'{"environment_id":"env-1"}',
            headers=_safe_pgadmin_headers(client),
        )
    assert response.status_code == 200
    assert response.json() == {"state": "started", "url": "http://127.0.0.1:5050"}
    assert opener_calls == ["env-1"]


@pytest.mark.dashboard
def test_default_ui_composition_keeps_one_client_environment_resource() -> None:
    from odoo_instance_sdk.http.app import create_app

    app = create_app(headless=False, static_assets=False)
    assert app.state.odoo_client.environments._client is app.state.odoo_client
    assert "/api/v1/pgadmin/open" in app.openapi()["paths"]


@pytest.mark.dashboard
def test_headless_composition_excludes_pgadmin_route() -> None:
    from odoo_instance_sdk.http.app import create_app

    app = create_app(
        headless=True,
        monitor=_schema_monitor(
            Snapshot(schema_version=3, generated_at=datetime.now(UTC), projects=(), environments=())
        ),
        pgadmin_opener=lambda _: pytest.fail("headless app must not construct or call opener"),
    )
    assert "/api/v1/pgadmin/open" not in app.openapi()["paths"]


@pytest.mark.parametrize(
    "body",
    [b"{", b"{}", b'{"environment_id": 3}', b'{"environment_id":"env","extra":true}'],
    ids=["malformed", "missing", "wrong-type", "unknown-field"],
)
@pytest.mark.dashboard
def test_pgadmin_invalid_requests_are_identical_and_do_not_delegate(body: bytes) -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    calls: list[str] = []

    def opener(environment_id: str) -> PgAdminOpenResult:
        calls.append(environment_id)
        return PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://127.0.0.1:5050")

    app = create_app(
        headless=False,
        static_assets=False,
        monitor=_schema_monitor(
            Snapshot(schema_version=3, generated_at=datetime.now(UTC), projects=(), environments=())
        ),
        pgadmin_opener=opener,
    )
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/v1/pgadmin/open",
            content=body,
            headers=_safe_pgadmin_headers(client),
        )
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_request", "message": "invalid request"}
    assert "environment" not in response.text
    assert "extra" not in response.text
    assert calls == []


@pytest.mark.dashboard
def test_pgadmin_browser_boundary_rejects_unsafe_requests_without_delegation() -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    calls: list[str] = []

    def opener(environment_id: str) -> PgAdminOpenResult:
        calls.append(environment_id)
        return PgAdminOpenResult(state=PgAdminOpenState.STARTED, url="http://127.0.0.1:5050")

    app = create_app(headless=False, static_assets=False, pgadmin_opener=opener)
    with TestClient(app, base_url="http://localhost") as client:
        safe = _safe_pgadmin_headers(client)
        cases = (
            {**safe, "Origin": "https://attacker.example"},
            {**safe, "Origin": "http://localhost/"},
            {**safe, "Origin": "http://localhost:1234"},
            {key: value for key, value in safe.items() if key != "Origin"},
            {**safe, "Content-Type": "text/plain"},
            {**safe, "Sec-Fetch-Site": "cross-site"},
            {**safe, "X-CSRF-Token": "wrong-token"},
        )
        for headers in cases:
            response = client.post(
                "/api/v1/pgadmin/open",
                content=b'{"environment_id":"env-1"}',
                headers=headers,
            )
            assert response.status_code == 422
            assert response.json() == {"code": "invalid_request", "message": "invalid request"}
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PgAdminEnvironmentNotFoundError, 404, "environment_not_found"),
        (PgAdminNotEligibleError, 409, "pgadmin_not_eligible"),
        (PgAdminDatabaseNotFoundError, 409, "database_not_found"),
        (PgAdminUnavailableError, 503, "pgadmin_unavailable"),
    ],
)
@pytest.mark.dashboard
def test_pgadmin_domain_errors_have_fixed_typed_responses(
    error: type[Exception], status: int, code: str
) -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    def opener(_: str) -> PgAdminOpenResult:
        raise error()

    app = create_app(headless=False, static_assets=False, pgadmin_opener=opener)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/v1/pgadmin/open",
            content=b'{"environment_id":"env-1"}',
            headers=_safe_pgadmin_headers(client),
        )
    assert response.status_code == status
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message"}


@pytest.mark.dashboard
def test_pgadmin_openapi_refs_and_success_bytes_are_production_typed() -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    result = PgAdminOpenResult(state=PgAdminOpenState.RECONFIGURED, url="http://127.0.0.1:5050")
    app = create_app(headless=False, static_assets=False, pgadmin_opener=lambda _: result)
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/pgadmin/open"]["post"]
    assert operation["operationId"] == "openPgAdmin"
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PgAdminOpenRequest"
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PgAdminOpenResult"
    }
    for response in operation["responses"].values():
        response_schema = response["content"]["application/json"]["schema"]
        assert (
            response_schema["$ref"].removeprefix("#/components/schemas/")
            in schema["components"]["schemas"]
        )
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/v1/pgadmin/open",
            content=b'{"environment_id":"env-1"}',
            headers=_safe_pgadmin_headers(client),
        )
    payload = json.loads(response.content)
    _validate_json(
        payload,
        schema["components"]["schemas"]["PgAdminOpenResult"],
        schema["components"]["schemas"],
    )
