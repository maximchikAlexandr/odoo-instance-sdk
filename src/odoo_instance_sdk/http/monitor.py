from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from typing import Any, Protocol, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    MonitorError,
    PgAdminDatabaseNotFoundError,
    PgAdminEnvironmentNotFoundError,
    PgAdminNotEligibleError,
    PgAdminUnavailableError,
)
from odoo_instance_sdk.internal.sanitize import sanitize_last_error
from odoo_instance_sdk.models import (
    HttpError,
    HttpErrorCode,
    PgAdminOpenRequest,
    PgAdminOpenResult,
    Snapshot,
)

_LOGGER = logging.getLogger(__name__)

__all__ = ["build_monitor_router", "build_pgadmin_router", "install_openapi_schema"]


class SnapshotProvider(Protocol):
    def snapshot(self, project_id: str | None = None) -> object: ...


PgAdminOpener = Callable[[str], PgAdminOpenResult]


def _replace_refs(value: object) -> object:
    if isinstance(value, dict):
        return {key: _replace_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return value.replace("#/$defs/", "#/components/schemas/", 1)
    return value


def _msgspec_components(*models: type[Any]) -> dict[str, dict[str, Any]]:
    """Return one stable OpenAPI component set for the supplied msgspec models."""
    components: dict[str, dict[str, Any]] = {}
    for model in models:
        document = msgspec.json.schema(model)
        definitions = document.get("$defs", {})
        for name, definition in definitions.items():
            converted = _replace_refs(definition)
            if not isinstance(converted, dict):
                raise TypeError(f"msgspec schema definition {name!r} is not an object")
            previous = components.get(name)
            if previous is not None and previous != converted:
                raise ValueError(f"duplicate OpenAPI component name: {name}")
            components[name] = converted
    return components


def _typed_response(model: type[Any]) -> dict[str, Any]:
    return {
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{model.__name__}"}}
        }
    }


def build_monitor_router(monitor: SnapshotProvider) -> Any:
    """Build the read-only monitor router without importing FastAPI eagerly."""
    from fastapi import APIRouter, Query
    from fastapi.responses import Response

    router = APIRouter()
    snapshot_lock = threading.Lock()

    def snapshot(project_id: str | None = Query(default=None)) -> Any:
        try:
            with snapshot_lock:
                snap = monitor.snapshot(project_id=project_id)
        except MonitorError:
            error = HttpError(
                code=HttpErrorCode.monitor_snapshot_failed,
                message="monitor snapshot failed",
            )
            return Response(
                content=msgspec.json.encode(error),
                media_type="application/json",
                status_code=500,
            )
        except Exception as exc:
            # Keep diagnostics useful for local logs without allowing an
            # internal path, secret, or exception text into the HTTP body.
            _LOGGER.warning(
                "monitor snapshot failed: %s",
                sanitize_last_error(str(exc)) or type(exc).__name__,
            )
            error = HttpError(
                code=HttpErrorCode.monitor_snapshot_failed,
                message="monitor snapshot failed",
            )
            return Response(
                content=msgspec.json.encode(error),
                media_type="application/json",
                status_code=500,
            )
        return Response(content=msgspec.json.encode(snap), media_type="application/json")

    router.get("/api/v1/snapshot", operation_id="getMonitorSnapshot")(snapshot)

    return router


def build_pgadmin_router(opener: PgAdminOpener) -> Any:  # noqa: C901
    """Build the UI-only state-changing route around one typed opener."""
    from fastapi import APIRouter, Request
    from fastapi.responses import Response

    # FastAPI resolves postponed annotations against this module's globals;
    # keep the import lazy while making the request sentinel visible to it.
    globals()["Request"] = Request
    router = APIRouter()

    def error(code: HttpErrorCode, status_code: int) -> Response:
        messages = {
            HttpErrorCode.invalid_request: "invalid request",
            HttpErrorCode.environment_not_found: "environment not found",
            HttpErrorCode.pgadmin_not_eligible: "pgAdmin is not eligible for this environment",
            HttpErrorCode.database_not_found: "selected database was not found",
            HttpErrorCode.pgadmin_unavailable: "pgAdmin is unavailable",
        }
        return Response(
            content=msgspec.json.encode(HttpError(code=code, message=messages[code])),
            media_type="application/json",
            status_code=status_code,
        )

    def request_is_safe(request: Any) -> bool:
        """Apply browser request-boundary checks before decoding or delegation."""
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return False
        if not _same_origin(request):
            return False
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site.lower() != "same-origin":
            return False
        token = request.headers.get("x-csrf-token")
        cookie = request.cookies.get("odoo_instance_sdk_csrf")
        return bool(token and cookie and secrets.compare_digest(token, cookie))

    async def open_pgadmin(request: Request) -> Any:
        if not request_is_safe(request):
            return error(HttpErrorCode.invalid_request, 422)
        try:
            payload = msgspec.json.decode(
                await request.body(), type=PgAdminOpenRequest, strict=True
            )
        except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError):
            return error(HttpErrorCode.invalid_request, 422)
        try:
            result = opener(payload.environment_id)
        except PgAdminEnvironmentNotFoundError:
            return error(HttpErrorCode.environment_not_found, 404)
        except (PgAdminNotEligibleError, PgAdminDatabaseNotFoundError) as exc:
            code = (
                HttpErrorCode.database_not_found
                if isinstance(exc, PgAdminDatabaseNotFoundError)
                else HttpErrorCode.pgadmin_not_eligible
            )
            return error(code, 409)
        except PgAdminUnavailableError:
            return error(HttpErrorCode.pgadmin_unavailable, 503)
        except Exception:
            return error(HttpErrorCode.pgadmin_unavailable, 503)
        return Response(content=msgspec.json.encode(result), media_type="application/json")

    router.post("/api/v1/pgadmin/open", operation_id="openPgAdmin")(open_pgadmin)

    return router


def _same_origin(request: Any) -> bool:
    """Require an explicit Origin that exactly matches the request origin."""
    from urllib.parse import urlsplit

    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return False
    try:
        parsed = urlsplit(origin)
        origin_port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
    ):
        return False
    request_scheme = request.url.scheme.lower()
    request_host = request.url.hostname
    request_port = request.url.port
    if not request_host or request_scheme not in {"http", "https"}:
        return False
    default_ports = {"http": 80, "https": 443}
    return (
        parsed.scheme.lower() == request_scheme
        and parsed.hostname.lower() == request_host.lower()
        and (origin_port or default_ports[parsed.scheme.lower()])
        == (request_port or default_ports[request_scheme])
    )


def install_openapi_schema(app: Any) -> None:
    """Install deterministic schemas and typed snapshot response metadata."""
    from fastapi.openapi.utils import get_openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return cast("dict[str, Any]", app.openapi_schema)
        schema: dict[str, Any] = get_openapi(
            title="odoo-instance-sdk monitor",
            version="0.1.0",
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("schemas", {}).update(
            _msgspec_components(Snapshot, HttpError, PgAdminOpenRequest, PgAdminOpenResult)
        )
        operation = schema["paths"]["/api/v1/snapshot"]["get"]
        operation["operationId"] = "getMonitorSnapshot"
        operation["responses"]["200"] = {
            "description": "Successful Response",
            **_typed_response(Snapshot),
        }
        operation["responses"]["500"] = {
            "description": "Monitor snapshot failed",
            **_typed_response(HttpError),
        }
        pgadmin_operation = schema["paths"].get("/api/v1/pgadmin/open", {}).get("post")
        if pgadmin_operation is not None:
            pgadmin_operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/PgAdminOpenRequest"}
                    }
                },
            }
            pgadmin_operation["responses"] = {
                "200": {"description": "pgAdmin opened", **_typed_response(PgAdminOpenResult)},
                "404": {
                    "description": "Environment not found",
                    **_typed_response(HttpError),
                },
                "409": {
                    "description": "pgAdmin operation rejected",
                    **_typed_response(HttpError),
                },
                "422": {
                    "description": "Invalid request",
                    **_typed_response(HttpError),
                },
                "503": {
                    "description": "pgAdmin unavailable",
                    **_typed_response(HttpError),
                },
            }
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
