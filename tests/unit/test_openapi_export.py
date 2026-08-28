from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.dashboard


def _exporter() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts" / "export_openapi.py"
    spec = importlib.util.spec_from_file_location("export_openapi", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_is_byte_identical_and_does_not_need_web_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter()
    from odoo_instance_sdk.http import app as app_module

    monkeypatch.setattr(app_module, "_WEB_DIST", tmp_path / "missing-dist")
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    exporter.export_openapi(first)
    exporter.export_openapi(second)

    first_bytes = first.read_bytes()
    assert first_bytes == second.read_bytes()
    assert first_bytes.endswith(b"\n")
    assert not first_bytes.endswith(b"\n\n")
    assert json.loads(first_bytes)["paths"]["/api/v1/pgadmin/open"]["post"]["operationId"] == (
        "openPgAdmin"
    )


def test_export_uses_static_free_typed_composition_without_runtime_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _exporter()
    calls: list[dict[str, Any]] = []
    real_create_app = exporter.create_app

    def record_create_app(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return real_create_app(**kwargs)

    monkeypatch.setattr(exporter, "create_app", record_create_app)
    document = exporter.build_openapi()

    assert len(calls) == 1
    assert calls[0]["headless"] is False
    assert calls[0]["static_assets"] is False
    assert calls[0]["monitor"].__class__.__name__ == "_SchemaMonitor"
    assert calls[0]["pgadmin_opener"] is exporter._schema_pgadmin_opener
    assert document["paths"]["/api/v1/pgadmin/open"]["post"]["operationId"] == "openPgAdmin"
    rendered = json.dumps(document, sort_keys=True)
    assert "127.0.0.1" not in rendered
    assert "localhost" not in rendered
    assert "/Users/" not in rendered
    assert "pgadmin4.db" not in rendered


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-27T10:00:00Z",
        "http://127.0.0.1:5050",
        "/Users/admin/private",
        "12345678-1234-4234-8234-123456789abc",
    ],
)
def test_export_safety_gate_rejects_volatile_or_machine_local_values(value: str) -> None:
    exporter = _exporter()
    with pytest.raises(ValueError):
        exporter._reject_unsafe({"value": value})


@pytest.mark.parametrize("key", ["password", "secret", "id", "identifier"])
def test_export_safety_gate_rejects_sensitive_or_random_keyed_values(key: str) -> None:
    exporter = _exporter()
    with pytest.raises(ValueError):
        exporter._reject_unsafe({key: "value-that-must-not-be-exported"})


def test_export_safety_gate_rejects_keyed_local_port() -> None:
    exporter = _exporter()
    with pytest.raises(ValueError):
        exporter._reject_unsafe({"port": "5050"})


def test_export_safety_gate_rejects_machine_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _exporter()
    monkeypatch.setenv("OPENAPI_SECRET_SENTINEL", "environment-secret-sentinel")

    with pytest.raises(ValueError, match="environment value"):
        exporter._reject_unsafe({"description": "environment-secret-sentinel"})


def test_export_safety_gate_ignores_unrelated_ci_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _exporter()
    monkeypatch.setenv("GITHUB_JOB", "generated-contract-check")

    exporter._reject_unsafe({"description": "generated-contract-check"})


def test_export_safety_gate_rejects_nested_path_values() -> None:
    exporter = _exporter()

    with pytest.raises(ValueError):
        exporter._reject_unsafe(
            {"paths": {"/api/v1/snapshot": {"description": "/Users/admin/private"}}}
        )
    with pytest.raises(ValueError):
        exporter._reject_unsafe(
            {
                "paths": {
                    "/api/v1/snapshot": {
                        "responses": {"200": {"description": "https://example.test"}}
                    }
                }
            }
        )


def test_export_safety_gate_allows_path_keys_and_refs() -> None:
    exporter = _exporter()

    exporter._reject_unsafe(
        {
            "paths": {
                "/api/v1/items/{item_id}": {
                    "responses": {"200": {"$ref": "#/components/schemas/Item"}}
                }
            }
        }
    )
