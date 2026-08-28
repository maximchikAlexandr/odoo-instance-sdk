"""Export the deterministic, runtime-independent dashboard OpenAPI document."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, cast

from odoo_instance_sdk.http.app import create_app
from odoo_instance_sdk.models import PgAdminOpenResult

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPOSITORY_ROOT / "openapi.json"

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$")
_ABSOLUTE_MACHINE_PATH = re.compile(
    r"^(?:/(?:Users|home|var|tmp|private|opt|etc|run)(?:/|$)|[A-Za-z]:[\\/])"
)
_MACHINE_ENV_MARKERS = (
    "PATH",
    "HOME",
    "HOST",
    "URL",
    "PORT",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
)


class _SchemaMonitor:
    """Typed stub: schema construction must never call a live monitor."""

    def snapshot(self, project_id: str | None = None) -> object:
        raise AssertionError("schema export must not collect a monitor snapshot")


def _schema_pgadmin_opener(environment_id: str) -> PgAdminOpenResult:
    raise AssertionError("schema export must not open pgAdmin")


def build_openapi() -> dict[str, Any]:
    """Build OpenAPI exclusively through the static-free production app."""
    return cast(
        "dict[str, Any]",
        create_app(
            headless=False,
            static_assets=False,
            monitor=_SchemaMonitor(),
            pgadmin_opener=_schema_pgadmin_opener,
        ).openapi(),
    )


def _reject_unsafe(value: object, *, key: str = "") -> None:  # noqa: C901
    """Reject volatile or machine-local JSON values recursively.

    OpenAPI path templates are dictionary keys, not values.  They therefore
    do not need an exemption that could accidentally be inherited by nested
    operation or schema values.
    """
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _reject_unsafe(child_value, key=str(child_key))
        return
    if isinstance(value, list):
        for child in value:
            _reject_unsafe(child, key=key)
        return
    if isinstance(value, bool) or value is None:
        return
    key_name = key.lower()
    if isinstance(value, int):
        if key_name in {"port", "hostport"} and 1 <= value <= 65535:
            raise ValueError("machine-local port in OpenAPI document")
        return
    if not isinstance(value, str):
        raise TypeError("OpenAPI document contains a non-JSON scalar")
    if value.startswith("#/"):
        return
    if _UUID.fullmatch(value) or _TIMESTAMP.fullmatch(value):
        raise ValueError("volatile identifier or timestamp in OpenAPI document")
    if (
        value.startswith("/")
        or _ABSOLUTE_MACHINE_PATH.match(value)
        or "\\Users\\" in value
        or "\\home\\" in value
    ):
        raise ValueError("machine-local path in OpenAPI document")
    machine_environment_values = {
        item
        for name, item in os.environ.items()
        if len(item) >= 4 and any(marker in name.upper() for marker in _MACHINE_ENV_MARKERS)
    }
    if value in machine_environment_values:
        raise ValueError("environment value in OpenAPI document")
    if value.startswith(("http://", "https://")):
        raise ValueError("machine-specific server URL in OpenAPI document")
    if key_name in {
        "host",
        "hostname",
        "url",
        "server",
        "servers",
        "socket",
        "password",
        "secret",
        "token",
        "credential",
        "credentials",
        "id",
        "identifier",
    }:
        raise ValueError("machine-specific integration value in OpenAPI document")
    if key_name in {"port", "hostport"} and value.isdigit() and 1 <= int(value) <= 65535:
        raise ValueError("machine-local port in OpenAPI document")


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    _reject_unsafe(document)
    return (json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_openapi(path: Path | None = None) -> Path:
    """Atomically export canonical OpenAPI and return its destination."""
    destination = OPENAPI_PATH if path is None else path
    _atomic_write(destination, _canonical_bytes(build_openapi()))
    return destination


if __name__ == "__main__":
    export_openapi()
