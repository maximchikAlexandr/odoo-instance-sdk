from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from unittest.mock import Mock

import msgspec
import pytest

from odoo_instance_sdk.models import HttpErrorCode, RuntimeActionRequest, Snapshot
from tests.unit.test_http_contract import _validate_json

pytestmark = pytest.mark.dashboard


FIXED_GENERATED_AT = datetime(2026, 8, 27, tzinfo=UTC)
RUNTIME_ACTIONS = (("start", "started"), ("stop", "stopped"))
OwnerKind = Literal["project", "environment"]
RUNTIME_OWNERS: tuple[tuple[OwnerKind, str], ...] = (("project", "p"), ("environment", "e"))
INVALID_RUNTIME_BODIES = (
    b"{",
    b"{}",
    b'{"owner_kind":"project","owner_id":3}',
    b'{"owner_kind":"project","owner_id":"p","port":8168}',
)


class _FixedMonitor:
    def snapshot(self, project_id: str | None = None) -> Snapshot:
        del project_id
        return Snapshot(
            schema_version=4,
            generated_at=FIXED_GENERATED_AT,
            projects=(),
            environments=(),
        )


def _runtime_headers(client: Any, *, origin: str = "http://localhost") -> dict[str, str]:
    cookies = client.cookies
    return {"Origin": origin, "X-CSRF-Token": cookies["odoo_instance_sdk_csrf"]}


@pytest.mark.parametrize(("action", "expected_state"), RUNTIME_ACTIONS)
@pytest.mark.parametrize(("owner_kind", "owner_id"), RUNTIME_OWNERS)
def test_runtime_success_delegates_typed_request(
    action: str, expected_state: str, owner_kind: OwnerKind, owner_id: str
) -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app
    from odoo_instance_sdk.models import RuntimeActionResult

    calls: list[tuple[str, RuntimeActionRequest]] = []

    def control(operation: str, target: RuntimeActionRequest) -> RuntimeActionResult:
        calls.append((operation, target))
        return RuntimeActionResult(state="started" if operation == "start" else "stopped")

    app = create_app(
        headless=False,
        static_assets=False,
        monitor=_FixedMonitor(),
        runtime_controller=control,
    )
    with TestClient(app, base_url="http://localhost") as client:
        client.get("/healthz")
        headers = _runtime_headers(client)
        path = f"/api/v1/runtime/{action}"
        response = client.post(
            path,
            json={"owner_kind": owner_kind, "owner_id": owner_id},
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json() == {"state": expected_state}
    assert msgspec.json.decode(response.content, type=RuntimeActionResult).state == expected_state
    operation = app.openapi()["paths"][path]["post"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    _validate_json(response.json(), response_schema, app.openapi()["components"]["schemas"])
    assert calls == [(action, RuntimeActionRequest(owner_kind=owner_kind, owner_id=owner_id))]


@pytest.mark.parametrize("action", [name for name, _ in RUNTIME_ACTIONS])
@pytest.mark.parametrize("raw_body", INVALID_RUNTIME_BODIES)
def test_runtime_invalid_bodies_never_delegate(action: str, raw_body: bytes) -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    controller = Mock()
    app = create_app(
        headless=False, static_assets=False, monitor=_FixedMonitor(), runtime_controller=controller
    )
    with TestClient(app, base_url="http://localhost") as client:
        client.get("/healthz")
        response = client.post(
            f"/api/v1/runtime/{action}",
            content=raw_body,
            headers={
                **_runtime_headers(client),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_request", "message": "invalid request"}
    operation = app.openapi()["paths"][f"/api/v1/runtime/{action}"]["post"]
    _validate_json(
        response.json(),
        operation["responses"]["422"]["content"]["application/json"]["schema"],
        app.openapi()["components"]["schemas"],
    )
    controller.assert_not_called()


RUNTIME_UNSAFE_HEADERS = (
    ("foreign origin", {"Origin": "https://attacker.test"}),
    ("wrong csrf", {"X-CSRF-Token": "wrong"}),
    ("missing origin", {"Origin": None}),
)


@pytest.mark.parametrize("action", [name for name, _ in RUNTIME_ACTIONS])
@pytest.mark.parametrize(("case", "overrides"), RUNTIME_UNSAFE_HEADERS)
def test_runtime_origin_and_csrf_rejection(
    action: str, case: str, overrides: dict[str, str | None]
) -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app

    controller = Mock()
    app = create_app(
        headless=False, static_assets=False, monitor=_FixedMonitor(), runtime_controller=controller
    )
    with TestClient(app, base_url="http://localhost") as client:
        client.get("/healthz")
        headers = {**_runtime_headers(client), "Content-Type": "application/json"}
        for name, value in overrides.items():
            if value is None:
                headers.pop(name)
            else:
                headers[name] = value
        response = client.post(
            f"/api/v1/runtime/{action}",
            json={"owner_kind": "project", "owner_id": "p"},
            headers=headers,
        )
    assert response.status_code == 422, case
    assert response.json() == {"code": "invalid_request", "message": "invalid request"}
    controller.assert_not_called()


@pytest.mark.parametrize("action", [name for name, _ in RUNTIME_ACTIONS])
def test_runtime_openapi_shape(action: str) -> None:
    from odoo_instance_sdk.http.app import create_app

    app = create_app(
        headless=False, static_assets=False, monitor=_FixedMonitor(), runtime_controller=Mock()
    )
    operation = app.openapi()["paths"][f"/api/v1/runtime/{action}"]["post"]
    assert operation["operationId"] == f"{action}Runtime"
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RuntimeActionRequest"
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RuntimeActionResult"
    }
    assert set(operation["responses"]) == {"200", "404", "409", "422", "503"}


RUNTIME_ERRORS: tuple[tuple[Exception, int, HttpErrorCode, str], ...] = (
    (LookupError("private path"), 404, HttpErrorCode.runtime_not_found, "runtime not found"),
    (
        RuntimeError("password=secret"),
        409,
        HttpErrorCode.runtime_conflict,
        "Runtime action rejected. Check the instance configuration, dependencies and runtime ownership.",
    ),
    (ValueError("secret"), 503, HttpErrorCode.runtime_unavailable, "runtime operation unavailable"),
)


@pytest.mark.parametrize("action", [name for name, _ in RUNTIME_ACTIONS])
@pytest.mark.parametrize(("error", "status", "code", "message"), RUNTIME_ERRORS)
def test_runtime_errors_are_typed_and_sanitized(
    action: str, error: Exception, status: int, code: HttpErrorCode, message: str
) -> None:
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import create_app
    from odoo_instance_sdk.models import HttpError

    def control(operation, target):
        raise error

    app = create_app(
        headless=False,
        static_assets=False,
        monitor=_FixedMonitor(),
        runtime_controller=control,
    )
    with TestClient(app, base_url="http://localhost") as client:
        client.get("/healthz")
        response = client.post(
            f"/api/v1/runtime/{action}",
            json={"owner_kind": "project", "owner_id": "p"},
            headers={
                **_runtime_headers(client),
            },
        )
    assert response.status_code == status
    assert msgspec.json.decode(response.content, type=HttpError) == HttpError(
        code=code, message=message
    )
    operation = app.openapi()["paths"][f"/api/v1/runtime/{action}"]["post"]
    _validate_json(
        response.json(),
        operation["responses"][str(status)]["content"]["application/json"]["schema"],
        app.openapi()["components"]["schemas"],
    )
    assert "secret" not in response.text and "private" not in response.text


def _runtime_controller_case(
    owner_kind: OwnerKind,
    owner_id: str,
    factory_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, RuntimeActionRequest, Any, Any, Any, str, Any]:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.http.runtime import RuntimeController
    from odoo_instance_sdk.project import ProjectConfig

    client = Mock(spec=OdooClient)
    client.get_catalog.return_value.get_project.return_value = {
        "repository_root": "/registered/project"
    }
    environment = Mock(project_id="p")
    client.environments = Mock()
    client.environments.get.return_value = environment
    client.instance = Mock()
    instance = Mock()
    client.instance.from_project.return_value = instance
    client.instance.from_environment.return_value = instance
    monitor = Mock()
    runtime = Mock(state="stopped")
    monitor.snapshot.return_value = Mock(
        projects=[Mock(id="p", runtime=runtime)], environments=[Mock(id="e", runtime=runtime)]
    )
    project = Mock()
    monkeypatch.setattr(ProjectConfig, "load", Mock(return_value=project))
    monkeypatch.setattr("odoo_instance_sdk.client.OdooClient", Mock(return_value=client))
    controller = RuntimeController(OdooClientConfig(executable="odoo"), monitor)
    target = RuntimeActionRequest(owner_kind=owner_kind, owner_id=owner_id)
    expected_owner = {"from_project": project, "from_environment": environment}[factory_name]
    return controller, target, client, instance, runtime, factory_name, expected_owner


@pytest.mark.parametrize(
    ("owner_kind", "owner_id", "factory_name"),
    [("project", "p", "from_project"), ("environment", "e", "from_environment")],
)
def test_runtime_start_delegates_to_public_command(
    owner_kind: OwnerKind, owner_id: str, factory_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, target, client, instance, _runtime, _factory_name, expected_owner = (
        _runtime_controller_case(owner_kind, owner_id, factory_name, monkeypatch)
    )
    assert controller("start", target).state == "started"
    instance.run_detached_command.return_value.run.assert_called_once()
    getattr(client.instance, factory_name).assert_called_once_with(expected_owner)


@pytest.mark.parametrize("owner_kind", ["project", "environment"])
def test_runtime_duplicate_start_is_rejected(
    owner_kind: OwnerKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id, factory_name = {
        "project": ("p", "from_project"),
        "environment": ("e", "from_environment"),
    }[owner_kind]
    controller, target, _client, instance, runtime, _factory_name, _expected_owner = (
        _runtime_controller_case(owner_kind, owner_id, factory_name, monkeypatch)
    )
    assert controller("start", target).state == "started"
    runtime.state = "not_ready"
    with pytest.raises(RuntimeError, match="already running"):
        controller("start", target)
    instance.run_detached_command.assert_called_once()


@pytest.mark.parametrize(
    ("owner_kind", "owner_id", "factory_name"),
    [("project", "p", "from_project"), ("environment", "e", "from_environment")],
)
def test_runtime_stop_delegates_to_public_command(
    owner_kind: OwnerKind, owner_id: str, factory_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, target, client, instance, _runtime, _factory_name, expected_owner = (
        _runtime_controller_case(owner_kind, owner_id, factory_name, monkeypatch)
    )
    assert controller("stop", target).state == "stopped"
    instance.stop_runtime_command.return_value.run.assert_called_once()
    getattr(client.instance, factory_name).assert_called_once_with(expected_owner)


def test_runtime_invalid_action_rejects_before_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from odoo_instance_sdk.http.runtime import RuntimeController

    controller, target, client, instance, _runtime, _factory_name, _expected_owner = (
        _runtime_controller_case("project", "p", "from_project", monkeypatch)
    )
    assert isinstance(controller, RuntimeController)
    with pytest.raises(ValueError, match="unsupported runtime action"):
        controller(cast("Any", "restart"), target)
    client.instance.from_project.assert_not_called()
    instance.stop_runtime_command.assert_not_called()


@pytest.mark.parametrize("external", [False, True])
def test_runtime_mutations_require_ui_mode(external):
    from odoo_instance_sdk.http.app import ExternalProxyConfig, create_app

    app = create_app(
        headless=not external,
        static_assets=False,
        external_proxy=ExternalProxyConfig(
            allowed_hosts=("panel.example.test",), trusted_proxy_addresses=("testclient",)
        )
        if external
        else None,
    )
    assert ("/api/v1/runtime/start" in app.openapi()["paths"]) is external
    assert ("/api/v1/runtime/stop" in app.openapi()["paths"]) is external


@pytest.mark.parametrize("action", ["start", "stop"])
def test_external_runtime_actions_require_trusted_proxy_and_exact_https_origin(action):
    from fastapi.testclient import TestClient

    from odoo_instance_sdk.http.app import ExternalProxyConfig, create_app
    from odoo_instance_sdk.models import RuntimeActionResult

    calls = []

    def control(operation, target):
        calls.append(operation)
        return RuntimeActionResult(state="started" if operation == "start" else "stopped")

    app = create_app(
        headless=False,
        static_assets=False,
        runtime_controller=control,
        external_proxy=ExternalProxyConfig(
            allowed_hosts=("panel.example.test",), trusted_proxy_addresses=("testclient",)
        ),
    )
    with TestClient(app, base_url="https://panel.example.test") as client:
        forwarding = {"X-Forwarded-Host": "panel.example.test", "X-Forwarded-Proto": "https"}
        assert client.get("/healthz", headers=forwarding).status_code == 200
        headers = {
            **forwarding,
            "Origin": "https://panel.example.test",
            "X-CSRF-Token": client.cookies["odoo_instance_sdk_csrf"],
        }
        body = {"owner_kind": "project", "owner_id": "p"}
        path = f"/api/v1/runtime/{action}"
        assert client.post(path, json=body, headers=headers).status_code == 200
        for origin in (
            "http://panel.example.test",
            "https://attacker.test",
            "https://panel.example.test:1234",
            "https://panel.example.test:0",
        ):
            assert (
                client.post(path, json=body, headers={**headers, "Origin": origin}).status_code
                == 422
            )
        assert (
            client.post(path, json=body, headers={**headers, "X-CSRF-Token": "wrong"}).status_code
            == 422
        )
        assert (
            client.post(
                path, json=body, headers={**headers, "X-Forwarded-Host": "attacker.test"}
            ).status_code
            == 400
        )
    assert calls == [action]


@pytest.mark.parametrize("owner_kind", ["project", "environment"])
def test_missing_runtime_owner_cannot_launch_or_stop(owner_kind, monkeypatch):
    from unittest.mock import Mock

    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.exceptions import EnvironmentNotFoundError
    from odoo_instance_sdk.http.runtime import RuntimeController
    from odoo_instance_sdk.models import RuntimeActionRequest

    client = Mock(spec=OdooClient)
    client.instance = Mock()
    client.environments = Mock()
    client.get_catalog.return_value.get_project.return_value = None
    client.environments.get.side_effect = EnvironmentNotFoundError("missing")
    monkeypatch.setattr("odoo_instance_sdk.client.OdooClient", Mock(return_value=client))
    monitor = Mock()
    controller = RuntimeController(OdooClientConfig(executable="odoo"), monitor)
    target = RuntimeActionRequest(owner_kind=owner_kind, owner_id="missing")
    for operation in ("start", "stop"):
        with pytest.raises(LookupError, match="not found"):
            controller(operation, target)
    client.instance.from_project.assert_not_called()
    client.instance.from_environment.assert_not_called()
    monitor.snapshot.assert_not_called()
