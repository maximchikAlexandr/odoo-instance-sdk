"""Typed route state and reconciliation for the OdCLI-owned Caddy file."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from textwrap import indent
from time import monotonic
from typing import TYPE_CHECKING, Literal, NoReturn, TypeVar
from urllib.parse import urlsplit

from odoo_instance_sdk.exceptions import PublicationError
from odoo_instance_sdk.internal.locks import exclusive_lock_until, publication_lock_path
from odoo_instance_sdk.internal.paths import get_publication_config_path
from odoo_instance_sdk.internal.proc import ProcessResult, ProcessResultLike, RunContext
from odoo_instance_sdk.internal.sanitize import sanitize_last_error

if TYPE_CHECKING:
    from odoo_instance_sdk.resources.publication import PublicationSettings

_ROUTE_MARKER = "# odcli-managed-route-file-v1"
_ROUTE_DATA_PREFIX = "# odcli-routes-json: "
_T = TypeVar("_T")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)


def validate_route(route: PublicationRoute) -> None:
    """Reject persisted or constructed values before they reach Caddy text."""
    if route.owner_kind not in {"project", "environment", "panel"}:
        raise PublicationError("owned Caddy route has an invalid owner kind")
    for name, value in (
        ("owner_id", route.owner_id),
        ("project_id", route.project_id),
        ("environment_id", route.environment_id),
        ("route_identity", route.route_identity),
    ):
        if value is not None and not _SAFE_ID.fullmatch(value):
            raise PublicationError(f"owned Caddy route has an unsafe {name}")
    for name, value in (
        ("local_endpoint", route.local_endpoint),
        ("external_url", route.external_url),
    ):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(char.isspace() or char in '{}\\"' for char in value)
        ):
            raise PublicationError(f"owned Caddy route has an unsafe {name}")
        if name == "external_url" and (
            parsed.scheme != "https"
            or parsed.hostname is None
            or not _SAFE_HOST.fullmatch(parsed.hostname)
        ):
            raise PublicationError("owned Caddy route has an unsafe external_url")


@dataclass(frozen=True, slots=True)
class PublicationRoute:
    owner_kind: Literal["project", "environment", "panel"]
    owner_id: str
    project_id: str | None
    environment_id: str | None
    local_endpoint: str
    external_url: str
    route_identity: str

    def json(self) -> dict[str, str | None]:
        return {
            "owner_kind": self.owner_kind,
            "owner_id": self.owner_id,
            "project_id": self.project_id,
            "environment_id": self.environment_id,
            "local_endpoint": self.local_endpoint,
            "external_url": self.external_url,
            "route_identity": self.route_identity,
        }


def route_identity(owner_kind: str, owner_id: str, url: str) -> str:
    return hashlib.sha256(f"{owner_kind}:{owner_id}:{url}".encode()).hexdigest()[:16]


def _invalid_route_file() -> NoReturn:
    raise PublicationError("owned Caddy route file is not an OdCLI route file")


def decode_routes(raw: bytes) -> list[PublicationRoute]:
    if not raw:
        return []
    try:
        lines = raw.decode("utf-8").splitlines()
        encoded = next(
            line[len(_ROUTE_DATA_PREFIX) :] for line in lines if line.startswith(_ROUTE_DATA_PREFIX)
        )
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode()).decode("utf-8"))
        routes = payload["routes"]
        if not isinstance(routes, list):
            return _invalid_route_file()
        result: list[PublicationRoute] = []
        for item in routes:
            if not isinstance(item, dict):
                return _invalid_route_file()
            route = PublicationRoute(**item)
            validate_route(route)
            result.append(route)
        return result  # noqa: TRY300
    except (
        StopIteration,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise PublicationError("owned Caddy route file is not an OdCLI route file") from exc


def read_owned_routes() -> tuple[PublicationRoute, ...]:
    """Read the one owned route file without probing runtimes or Caddy."""
    settings_path = get_publication_config_path()
    if not settings_path.is_file():
        return ()
    from odoo_instance_sdk.resources.publication import PublicationSettings

    settings = PublicationSettings.load(settings_path)
    route_path = settings.owned_route_file
    if not route_path.is_file():
        return ()
    return tuple(decode_routes(route_path.read_bytes()))


def root_imports_owned_route(root_config: Path, owned_route_file: Path) -> bool:
    """Prove that an existing Caddy root imports this exact route file."""
    try:
        content = root_config.read_text(encoding="utf-8")
        expected = owned_route_file.resolve(strict=False)
    except OSError:
        return False
    for line in content.splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            continue
        if len(tokens) != 2 or tokens[0] != "import":
            continue
        imported = Path(tokens[1]).expanduser()
        if not imported.is_absolute():
            imported = root_config.parent / imported
        try:
            if imported.resolve(strict=False) == expected:
                return True
        except OSError:
            continue
    return False


def _route_block(route: PublicationRoute, settings: PublicationSettings) -> str:
    validate_route(route)
    if route.owner_kind == "panel":
        return render_panel_route(route.local_endpoint, settings)
    host = urlsplit(route.external_url).netloc
    address = _site_address(host, settings)
    endpoint = route.local_endpoint.rstrip("/")
    auth = (
        f"    basic_auth {_hash_algorithm(settings)} {{\n"
        f"        {settings.basic_auth_username} {settings.basic_auth_password_hash}\n"
        "    }\n"
        if settings.odoo_basic_auth_enabled
        else ""
    )
    body = (
        "    encode gzip\n"
        f"{auth}"
        "    @odoo_bus path /websocket /longpolling/poll /web/bus/*\n"
        f"    reverse_proxy @odoo_bus {endpoint} {{\n"
        "        header_up X-Forwarded-Proto https\n"
        "        header_up X-Forwarded-Host {host}\n"
        "        header_up X-Real-IP {remote_host}\n"
        "    }\n"
        f"    reverse_proxy {endpoint} {{\n"
        "        header_up X-Forwarded-Proto https\n"
        "        header_up X-Forwarded-Host {host}\n"
        "        header_up X-Real-IP {remote_host}\n"
        "    }\n"
    )
    return _site_block(address, body, settings)


def render_panel_route(local_endpoint: str, settings: PublicationSettings) -> str:
    """Render the protected panel proxy block for the later monitor command."""
    endpoint = local_endpoint.rstrip("/")
    if not endpoint:
        raise PublicationError("panel local endpoint is missing")
    body = (
        "    encode gzip\n"
        f"    basic_auth {_hash_algorithm(settings)} {{\n"
        f"        {settings.basic_auth_username} {settings.basic_auth_password_hash}\n"
        "    }\n"
        f"    reverse_proxy {endpoint} {{\n"
        "        header_up X-Forwarded-Proto https\n"
        "        header_up X-Forwarded-Host {host}\n"
        "        header_up X-Real-IP {remote_host}\n"
        "    }\n"
    )
    return _site_block(
        _site_address(f"{settings.panel_host_label}.{settings.domain_suffix}", settings),
        body,
        settings,
    )


def _site_address(host: str, settings: PublicationSettings) -> str:
    if settings.upstream_http_port is None:
        return host
    return f"http://{host}:{settings.upstream_http_port}"


def _hash_algorithm(settings: PublicationSettings) -> str:
    return "argon2id" if settings.basic_auth_password_hash.startswith("$argon2id$") else "bcrypt"


def _site_binding(settings: PublicationSettings) -> str:
    if settings.upstream_bind_address is None:
        return ""
    return f"    bind {settings.upstream_bind_address}\n"


def _site_block(address: str, body: str, settings: PublicationSettings) -> str:
    binding = _site_binding(settings)
    if settings.upstream_http_port is None:
        return f"{address} {{\n{binding}{body}}}\n"
    peers = " ".join(settings.trusted_proxy_addresses)
    return (
        f"{address} {{\n"
        f"{binding}"
        f"    @odcli_trusted_proxy remote_ip {peers}\n"
        "    handle @odcli_trusted_proxy {\n"
        f"{indent(body, '    ')}"
        "    }\n"
        "    respond 403\n"
        "}\n"
    )


def _render_routes(routes: Sequence[PublicationRoute], settings: PublicationSettings) -> bytes:
    for route in routes:
        validate_route(route)
    ordered = sorted(routes, key=lambda route: (route.owner_kind, route.owner_id))
    payload = json.dumps(
        {"routes": [route.json() for route in ordered]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii")
    text = f"{_ROUTE_MARKER}\n{_ROUTE_DATA_PREFIX}{encoded}\n\n"
    text += "\n".join(_route_block(route, settings).rstrip("\n") for route in ordered)
    return (text.rstrip() + "\n").encode("utf-8")


def panel_route(local_endpoint: str, settings: PublicationSettings) -> PublicationRoute:
    endpoint = local_endpoint.strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise PublicationError("monitor local endpoint is invalid")
    external = f"https://{settings.panel_host_label}.{settings.domain_suffix}"
    identity = hashlib.sha256(f"panel:monitor:{external}".encode()).hexdigest()[:16]
    return PublicationRoute(
        owner_kind="panel",
        owner_id="monitor",
        project_id=None,
        environment_id=None,
        local_endpoint=endpoint,
        external_url=external,
        route_identity=identity,
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = path.parent
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _as_result(value: ProcessResultLike) -> ProcessResult:
    if not isinstance(value, ProcessResult):
        raise PublicationError("Caddy process returned no result")
    return value


def _require_success(result: ProcessResult, message: str) -> None:
    if result.returncode != 0:
        raise PublicationError(message)


def reconcile_route_file(  # noqa: C901
    context: RunContext[_T],
    settings: PublicationSettings,
    *,
    action_id: str,
    owner_kind: Literal["project", "environment", "panel"],
    owner_id: str,
    route: PublicationRoute | None,
    remove: bool,
) -> PublicationRoute | None:
    """Run the one locked route transaction shared by every publication caller."""
    context.action(action_id)
    path = settings.owned_route_file
    validation_id = f"{action_id}.caddy.validate"
    replacement_id = f"{action_id}.route.replace"
    reload_id = f"{action_id}.caddy.reload"
    restore_id = f"{action_id}.route.restore"
    restore_reload_id = f"{action_id}.caddy.reload.restore"
    candidate_path = path.with_name(f".{path.name}.candidate")
    with exclusive_lock_until(publication_lock_path(), monotonic() + 30.0):
        if settings.caddy_reload_config is not None and not root_imports_owned_route(
            settings.caddy_reload_config, path
        ):
            raise PublicationError("selected Caddy root does not import the owned route file")
        prior = path.read_bytes() if path.exists() else b""
        current = decode_routes(prior)
        matching = next(
            (
                item
                for item in current
                if item.owner_kind == owner_kind and item.owner_id == owner_id
            ),
            None,
        )
        if remove and matching is None:
            for step_id in (
                validation_id,
                replacement_id,
                reload_id,
                restore_id,
                restore_reload_id,
            ):
                context.skip(step_id)
            context.complete_action(action_id)
            return None
        updated = [
            item
            for item in current
            if not (item.owner_kind == owner_kind and item.owner_id == owner_id)
        ]
        if not remove:
            if route is None:
                raise PublicationError("publication route is missing")
            updated.append(route)
        candidate = _render_routes(updated, settings)
        _atomic_write(candidate_path, candidate)
        try:
            validation = _as_result(context.process(validation_id))
            _require_success(validation, "Caddy candidate validation failed")
            context.action(replacement_id)
            _atomic_write(path, candidate)
            context.complete_action(replacement_id)
            reloaded = _as_result(context.process(reload_id))
            _require_success(reloaded, "Caddy reload rejected the candidate")
        except BaseException as exc:
            changed = path.exists() and path.read_bytes() != prior
            if changed:
                context.action(restore_id)
                _atomic_write(path, prior)
                context.complete_action(restore_id)
                if context.planned(restore_reload_id) and not context.consumed(restore_reload_id):
                    try:
                        restored = _as_result(context.process(restore_reload_id))
                        _require_success(restored, "Caddy live rollback was not confirmed")
                    except BaseException as recovery_exc:
                        raise PublicationError(
                            "Caddy live rollback was not confirmed"
                        ) from recovery_exc
            else:
                if not context.consumed(restore_id):
                    context.skip(restore_id)
                if not context.consumed(restore_reload_id):
                    context.skip(restore_reload_id)
            if isinstance(exc, PublicationError):
                raise
            raise PublicationError(sanitize_last_error(str(exc)) or "publication failed") from exc
        finally:
            candidate_path.unlink(missing_ok=True)
        context.skip(restore_id)
        context.skip(restore_reload_id)
        context.complete_action(action_id)
        return matching


__all__ = [
    "PublicationRoute",
    "decode_routes",
    "panel_route",
    "read_owned_routes",
    "reconcile_route_file",
    "render_panel_route",
    "root_imports_owned_route",
    "route_identity",
    "validate_route",
]
