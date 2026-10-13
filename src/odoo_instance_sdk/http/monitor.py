from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Protocol, cast

import msgspec

from odoo_instance_sdk.exceptions import (
    MonitorError,
    OdooInstanceSdkError,
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
    RuntimeActionRequest,
    RuntimeActionResult,
    Snapshot,
)

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI, Request
    from fastapi.responses import Response

    from odoo_instance_sdk.execution import JsonValue

_LOGGER = logging.getLogger(__name__)

__all__ = ["build_monitor_router", "build_pgadmin_router", "install_openapi_schema"]


class SnapshotProvider(Protocol):
    def snapshot(self, project_id: str | None = None) -> Snapshot: ...


PgAdminOpener = Callable[[str], PgAdminOpenResult]
RuntimeAction = Literal["start", "stop"]
RuntimeControl = Callable[[RuntimeAction, RuntimeActionRequest], RuntimeActionResult]


def _replace_refs(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _replace_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return value.replace("#/$defs/", "#/components/schemas/", 1)
    return value


def _msgspec_components(*models: type[msgspec.Struct]) -> dict[str, dict[str, JsonValue]]:
    """Return one stable OpenAPI component set for the supplied msgspec models."""
    components: dict[str, dict[str, JsonValue]] = {}
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


def _typed_response(model: type[msgspec.Struct]) -> dict[str, JsonValue]:
    return {
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{model.__name__}"}}
        }
    }


def build_monitor_router(monitor: SnapshotProvider) -> APIRouter:
    """Build the read-only monitor router without importing FastAPI eagerly."""
    from fastapi import APIRouter, Query
    from fastapi.responses import Response

    # Nested route annotations are postponed to keep FastAPI optional.  Make
    # the concrete adapter type visible to FastAPI's resolver only after the
    # optional dependency has been imported.
    globals()["Response"] = Response
    router = APIRouter()
    snapshot_lock = threading.Lock()

    def snapshot(project_id: str | None = Query(default=None)) -> Response:
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


def build_pgadmin_router(
    opener: PgAdminOpener,
    *,
    external_origin: tuple[str, tuple[str, ...]] | None = None,
) -> APIRouter:
    """Build the UI-only state-changing route around one typed opener."""
    from fastapi import APIRouter, Request
    from fastapi.responses import Response

    # FastAPI resolves postponed annotations against this module's globals;
    # keep the import lazy while making the request sentinel visible to it.
    globals()["Request"] = Request
    globals()["Response"] = Response
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

    async def open_pgadmin(request: Request) -> Response:
        if not request_is_safe(request, external_origin=external_origin):
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


def request_is_safe(
    request: Request, *, external_origin: tuple[str, tuple[str, ...]] | None = None
) -> bool:
    """Apply browser request-boundary checks before decoding or delegation."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return False
    if not _same_origin(request, external_origin=external_origin):
        return False
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site.lower() != "same-origin":
        return False
    token = request.headers.get("x-csrf-token")
    cookie = request.cookies.get("odoo_instance_sdk_csrf")
    return bool(token and cookie and secrets.compare_digest(token, cookie))


def _same_origin(
    request: Request,
    *,
    external_origin: tuple[str, tuple[str, ...]] | None = None,
) -> bool:
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
    if external_origin is not None:
        expected_scheme, expected_hosts = external_origin
        return (
            parsed.scheme.lower() == expected_scheme
            and parsed.hostname.lower() in expected_hosts
            and origin_port in (None, 443)
        )
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


def install_openapi_schema(app: FastAPI) -> None:
    """Install deterministic schemas and typed snapshot response metadata."""
    from fastapi.openapi.utils import get_openapi

    def custom_openapi() -> dict[str, JsonValue]:
        if app.openapi_schema:
            return cast("dict[str, JsonValue]", app.openapi_schema)
        schema = cast(
            "dict[str, dict[str, JsonValue]]",
            get_openapi(
                title="odoo-instance-sdk monitor",
                version="0.1.0",
                routes=app.routes,
            ),
        )
        components = schema.setdefault("components", {})
        schemas = cast("dict[str, dict[str, JsonValue]]", components.setdefault("schemas", {}))
        schemas.update(
            _msgspec_components(
                Snapshot,
                HttpError,
                PgAdminOpenRequest,
                PgAdminOpenResult,
                RuntimeActionRequest,
                RuntimeActionResult,
            )
        )
        paths = schema["paths"]
        snapshot_path = cast("dict[str, JsonValue]", paths["/api/v1/snapshot"])
        operation = cast("dict[str, JsonValue]", snapshot_path["get"])
        operation["operationId"] = "getMonitorSnapshot"
        responses = cast("dict[str, JsonValue]", operation["responses"])
        responses["200"] = {
            "description": "Successful Response",
            **_typed_response(Snapshot),
        }
        responses["500"] = {
            "description": "Monitor snapshot failed",
            **_typed_response(HttpError),
        }
        pgadmin_path = paths.get("/api/v1/pgadmin/open")
        pgadmin_operation = pgadmin_path.get("post") if isinstance(pgadmin_path, dict) else None
        if isinstance(pgadmin_operation, dict):
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
        for action in ("start", "stop"):
            path = paths.get(f"/api/v1/runtime/{action}")
            if isinstance(path, dict) and isinstance(path.get("post"), dict):
                operation = cast("dict[str, JsonValue]", path["post"])
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/RuntimeActionRequest"}
                        }
                    },
                }
                operation["responses"] = {
                    "200": {
                        "description": "Runtime action completed",
                        **_typed_response(RuntimeActionResult),
                    },
                    **{
                        str(code): {"description": description, **_typed_response(HttpError)}
                        for code, description in (
                            (404, "Runtime not found"),
                            (409, "Runtime action rejected"),
                            (422, "Invalid request"),
                            (503, "Runtime unavailable"),
                        )
                    },
                }
        app.openapi_schema = schema
        return cast("dict[str, JsonValue]", schema)

    setattr(app, "openapi", custom_openapi)


def build_runtime_router(
    controller: RuntimeControl, *, external_origin: tuple[str, tuple[str, ...]] | None = None
) -> APIRouter:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import APIRouter, Request
    from fastapi.responses import Response

    globals()["Request"] = Request
    globals()["Response"] = Response
    router = APIRouter()
    # One worker preserves the SDK catalog connection's thread affinity.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="odcli-runtime")
    router.add_event_handler("shutdown", lambda: executor.shutdown(wait=True))

    async def perform(request: Request, action: RuntimeAction) -> Response:
        def error(code: HttpErrorCode, status: int, message: str) -> Response:
            return Response(
                content=msgspec.json.encode(HttpError(code=code, message=message)),
                status_code=status,
                media_type="application/json",
            )

        if not request_is_safe(request, external_origin=external_origin):
            return error(HttpErrorCode.invalid_request, 422, "invalid request")
        try:
            target = msgspec.json.decode(
                await request.body(), type=RuntimeActionRequest, strict=True
            )
        except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError):
            return error(HttpErrorCode.invalid_request, 422, "invalid request")
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                executor, controller, action, target
            )
        except LookupError:
            return error(HttpErrorCode.runtime_not_found, 404, "runtime not found")
        except (OdooInstanceSdkError, RuntimeError):
            return error(
                HttpErrorCode.runtime_conflict,
                409,
                "Runtime action rejected. Check the instance configuration, dependencies and runtime ownership.",
            )
        except Exception:
            return error(HttpErrorCode.runtime_unavailable, 503, "runtime operation unavailable")
        return Response(content=msgspec.json.encode(result), media_type="application/json")

    async def start(request: Request) -> Response:
        return await perform(request, "start")

    async def stop(request: Request) -> Response:
        return await perform(request, "stop")

    router.post("/api/v1/runtime/start", operation_id="startRuntime")(start)
    router.post("/api/v1/runtime/stop", operation_id="stopRuntime")(stop)
    return router
