"""Safe, single-file Caddy publication for project and environment runtimes.

Publication is intentionally a small resource.  The route file is the only
publication state; the catalog and project manifests remain authorities for
runtime and database identity.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import stat
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, cast
from urllib.parse import urlsplit

import msgspec

from odoo_instance_sdk.exceptions import ConfigError, PublicationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.locks import exclusive_lock, publication_lock_path
from odoo_instance_sdk.internal.paths import get_config_root, get_publication_config_path
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    ProcessResult,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.sanitize import sanitize_last_error

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.environment import DevelopmentEnvironment
    from odoo_instance_sdk.resources.instance import OdooInstance


_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_PASSWORD_HASH = re.compile(r"^\$(?:2[aby]|argon2(?:id|i|d))\$")
_ROUTE_MARKER = "# odcli-managed-route-file-v1"
_ROUTE_DATA_PREFIX = "# odcli-routes-json: "
_OWNER_KIND = Literal["project", "environment", "panel"]


def _config_file_is_private(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise ConfigError("publication settings are unavailable") from exc
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ConfigError("publication settings must be a private regular file")


def _host_suffix(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigError("publication domain_suffix must be a string")
    value = value.strip().lower().rstrip(".")
    if not value or "*" in value or any(char in value for char in "/:@"):
        raise ConfigError("publication domain_suffix must be a DNS suffix")
    labels = value.split(".")
    if any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise ConfigError("publication domain_suffix contains an invalid DNS label")
    return value


def _local_endpoint(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("publication caddy_control_endpoint is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ConfigError("publication caddy_control_endpoint must be a local HTTP URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("publication caddy_control_endpoint must use a valid port") from exc
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or port is None:
        raise ConfigError("publication caddy_control_endpoint must not contain a path")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except (ValueError, TypeError) as exc:
        raise ConfigError("publication caddy_control_endpoint must use a loopback address") from exc
    if not address.is_loopback:
        raise ConfigError("publication caddy_control_endpoint must use a loopback address")
    return f"{parsed.scheme}://{parsed.hostname}:{port}"


def _owned_path(value: object, *, root: Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ConfigError("publication owned_route_file is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve(strict=False)
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ConfigError("publication owned_route_file must stay below the OdCLI root") from exc
    if path == root.resolve():
        raise ConfigError("publication owned_route_file must be a file below the OdCLI root")
    return path


def _host_label(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DNS_LABEL.fullmatch(value.strip().lower()):
        raise ConfigError(f"publication {field} must be one DNS label")
    return value.strip().lower()


def _proxy_addresses(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ConfigError("publication trusted_proxy_addresses must not be empty")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigError("publication trusted proxy address must be an IP")
        try:
            address = ipaddress.ip_address(item.strip())
        except ValueError as exc:
            raise ConfigError("publication trusted proxy address must be an IP") from exc
        normalized = str(address)
        if normalized in result:
            raise ConfigError("publication trusted proxy addresses must be unique")
        result.append(normalized)
    return tuple(result)


class PublicationSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """Validated, immutable owner-global Caddy settings."""

    domain_suffix: str
    caddy_executable: str
    caddy_control_endpoint: str
    owned_route_file: Path
    basic_auth_username: str
    basic_auth_password_hash: str
    panel_host_label: str = "panel"
    trusted_proxy_addresses: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        root = get_config_root(ensure_exists=False).parent
        msgspec.structs.force_setattr(self, "domain_suffix", _host_suffix(self.domain_suffix))
        if not isinstance(self.caddy_executable, str) or not self.caddy_executable.strip():
            raise ConfigError("publication caddy_executable is required")
        msgspec.structs.force_setattr(
            self,
            "caddy_control_endpoint",
            _local_endpoint(self.caddy_control_endpoint),
        )
        msgspec.structs.force_setattr(
            self, "owned_route_file", _owned_path(self.owned_route_file, root=root)
        )
        if not isinstance(self.basic_auth_username, str) or not self.basic_auth_username.strip():
            raise ConfigError("publication basic_auth_username is required")
        if not isinstance(self.basic_auth_password_hash, str) or not _PASSWORD_HASH.match(
            self.basic_auth_password_hash
        ):
            raise ConfigError("publication basic_auth_password_hash must be a password hash")
        msgspec.structs.force_setattr(
            self, "panel_host_label", _host_label(self.panel_host_label, "panel_host_label")
        )
        msgspec.structs.force_setattr(
            self,
            "trusted_proxy_addresses",
            _proxy_addresses(self.trusted_proxy_addresses),
        )

    def __repr__(self) -> str:
        return (
            f"PublicationSettings(domain_suffix={self.domain_suffix!r}, "
            f"caddy_executable={self.caddy_executable!r}, "
            "caddy_control_endpoint=<redacted>, "
            f"owned_route_file={self.owned_route_file!r}, "
            f"basic_auth_username={self.basic_auth_username!r}, "
            "basic_auth_password_hash=<redacted>, "
            f"panel_host_label={self.panel_host_label!r}, "
            f"trusted_proxy_addresses={self.trusted_proxy_addresses!r})"
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> PublicationSettings:
        settings_path = Path(path) if path is not None else get_publication_config_path()
        if not settings_path.is_file():
            raise ConfigError("publication settings file is missing")
        _config_file_is_private(settings_path)
        try:
            with settings_path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError("publication settings are invalid") from exc
        data = raw.get("publication", raw)
        if not isinstance(data, dict):
            raise ConfigError("publication settings must be a TOML table")
        expected = {
            "domain_suffix",
            "caddy_executable",
            "caddy_control_endpoint",
            "owned_route_file",
            "basic_auth_username",
            "basic_auth_password_hash",
            "panel_host_label",
            "trusted_proxy_addresses",
        }
        if set(data) - expected:
            raise ConfigError("publication settings contain an unknown key")
        try:
            normalized = dict(data)
            route_file = normalized.get("owned_route_file")
            if isinstance(route_file, str):
                normalized["owned_route_file"] = Path(route_file)
            return msgspec.convert(normalized, type=cls)
        except (TypeError, msgspec.ValidationError) as exc:
            raise ConfigError("publication settings are incomplete") from exc


@dataclass(frozen=True, slots=True)
class PublicationTarget:
    owner_kind: Literal["project", "environment"]
    owner_id: str
    local_endpoint: str
    project_id: str | None = None
    environment_id: str | None = None
    ready: bool = False
    runtime: OdooInstance | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.owner_kind not in {"project", "environment"} or not self.owner_id.strip():
            raise PublicationError("publication owner identity is invalid")
        if self.owner_kind == "environment" and self.environment_id is None:
            object.__setattr__(self, "environment_id", self.owner_id)


class PublicationResult(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    owner_kind: Literal["project", "environment"]
    owner_id: str
    project_id: str | None
    environment_id: str | None
    local_endpoint: str | None
    external_url: str | None
    route_identity: str | None
    status: Literal["published", "already_published", "unpublished", "already_absent"]
    reason: str | None = None

    def __repr__(self) -> str:
        return (
            f"PublicationResult(owner_kind={self.owner_kind!r}, owner_id={self.owner_id!r}, "
            f"status={self.status!r}, external_url={self.external_url!r})"
        )


@dataclass(frozen=True, slots=True)
class _Route:
    owner_kind: Literal["project", "environment"]
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


def _label_for_target(target: PublicationTarget) -> str:
    if target.owner_kind == "environment":
        return f"env-{target.owner_id}"
    stable = target.owner_id.removeprefix("project_").replace("_", "-")
    label = f"project-{stable}"
    if not _DNS_LABEL.fullmatch(label):
        raise PublicationError("publication owner identity is not a DNS-safe stable ID")
    return label


def external_url(target: PublicationTarget, settings: PublicationSettings) -> str:
    return f"https://{_label_for_target(target)}.{settings.domain_suffix}"


def _route_identity(target: PublicationTarget, url: str) -> str:
    return hashlib.sha256(f"{target.owner_kind}:{target.owner_id}:{url}".encode()).hexdigest()[:16]


def _invalid_route_file() -> NoReturn:
    raise PublicationError("owned Caddy route file is not an OdCLI route file")


def _decode_routes(raw: bytes) -> list[_Route]:
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
        result: list[_Route] = []
        for item in routes:
            if not isinstance(item, dict):
                return _invalid_route_file()
            result.append(_Route(**item))
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


def _route_block(route: _Route, settings: PublicationSettings) -> str:
    host = urlsplit(route.external_url).netloc
    endpoint = route.local_endpoint.rstrip("/")
    return (
        f"{host} {{\n"
        "    encode gzip\n"
        "    basic_auth {\n"
        f"        {settings.basic_auth_username} {settings.basic_auth_password_hash}\n"
        "    }\n"
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
        "}\n"
    )


def render_panel_route(local_endpoint: str, settings: PublicationSettings) -> str:
    """Render the protected panel proxy block for the later monitor command."""
    endpoint = local_endpoint.rstrip("/")
    if not endpoint:
        raise PublicationError("panel local endpoint is missing")
    return (
        f"{settings.panel_host_label}.{settings.domain_suffix} {{\n"
        "    encode gzip\n"
        "    basic_auth {\n"
        f"        {settings.basic_auth_username} {settings.basic_auth_password_hash}\n"
        "    }\n"
        f"    reverse_proxy {endpoint} {{\n"
        "        header_up X-Forwarded-Proto https\n"
        "        header_up X-Forwarded-Host {host}\n"
        "        header_up X-Real-IP {remote_host}\n"
        "    }\n"
        "}\n"
    )


def _render_routes(routes: Sequence[_Route], settings: PublicationSettings) -> bytes:
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


def _result(
    target: PublicationTarget,
    *,
    status: Literal["published", "already_published", "unpublished", "already_absent"],
    route: _Route | None,
    reason: str | None = None,
) -> PublicationResult:
    return PublicationResult(
        owner_kind=target.owner_kind,
        owner_id=target.owner_id,
        project_id=target.project_id,
        environment_id=target.environment_id,
        local_endpoint=target.local_endpoint if route is not None else None,
        external_url=route.external_url if route is not None else None,
        route_identity=route.route_identity if route is not None else None,
        status=status,
        reason=reason,
    )


def _as_result(value: object) -> ProcessResult:
    if not isinstance(value, ProcessResult):
        raise PublicationError("Caddy process returned no result")
    return value


def _require_success(result: ProcessResult, message: str) -> None:
    if result.returncode != 0:
        raise PublicationError(message)


def _runtime_endpoint(runtime: OdooInstance) -> str:
    identity = runtime._read_runtime_identity()
    if identity is None or identity.vanished:
        raise PublicationError("runtime is stopped")
    runtime._validate_runtime_identity(identity)
    config = runtime.config.start_config
    if config is None:
        raise PublicationError("runtime readiness configuration is unavailable")
    from odoo_instance_sdk.resources.instance.runtime_identity import _socket_owned_by

    if not _socket_owned_by(config, identity.root_pid):
        raise PublicationError("runtime listener ownership is not proven")
    return runtime.config.base_url


def _probe_runtime_health(endpoint: str) -> None:
    from odoo_instance_sdk.internal.transport import TransportError
    from odoo_instance_sdk.internal.transport.factory import open_odoo_http_client

    try:
        with open_odoo_http_client(endpoint, timeout=2.0) as http:
            response = http.get(f"{endpoint}/web/health?db_server_status=true")
        _require_healthy_response(response.status_code)
        payload = response.json()
    except PublicationError:
        raise
    except (TransportError, TypeError, ValueError, OSError) as exc:
        raise PublicationError("runtime health probe failed") from exc
    if not isinstance(payload, dict) or payload.get("status") != "pass":
        raise PublicationError("runtime health probe failed")


def _require_healthy_response(status_code: int) -> None:
    if status_code != 200:
        raise PublicationError("runtime health probe failed")


def _assert_runtime_ready(target: PublicationTarget) -> None:
    """Prove the route backend still belongs to the captured runtime."""
    runtime = target.runtime
    if runtime is None:
        if not target.ready:
            raise PublicationError("runtime readiness must be proven before publication")
        return
    try:
        endpoint = _runtime_endpoint(runtime)
        _probe_runtime_health(endpoint)
    except PublicationError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PublicationError("runtime identity is not valid") from exc


def _assert_mutation_target(target: PublicationTarget, *, remove: bool) -> None:
    if not remove:
        _assert_runtime_ready(target)


class PublicationResource:
    """One publication resource attached to :class:`OdooClient`."""

    def __init__(self, *, _client: OdooClient) -> None:
        self._client = _client

    def _target(
        self,
        value: PublicationTarget
        | OdooInstance
        | DevelopmentEnvironment
        | ProjectConfig
        | Path
        | str,
    ) -> PublicationTarget:
        if isinstance(value, PublicationTarget):
            return value
        from odoo_instance_sdk.project import ProjectConfig
        from odoo_instance_sdk.resources.environment.checkout_planning import EnvironmentState
        from odoo_instance_sdk.resources.instance import OdooInstance

        if isinstance(value, OdooInstance):
            binding = value._runtime_binding
            if binding is None:
                raise PublicationError("runtime owner identity is unavailable")
            return PublicationTarget(
                owner_kind=binding.owner_kind,
                owner_id=binding.owner_id,
                project_id=binding.project_id,
                environment_id=value._environment_id,
                local_endpoint=value.config.base_url,
                runtime=value,
            )
        if isinstance(value, ProjectConfig):
            return self._target(self._client.instance.from_project(value))
        if isinstance(value, (Path, str)):
            return self._target(self._client.instance.from_project(ProjectConfig.load(value)))
        if value.state is not EnvironmentState.READY:
            raise PublicationError(f"environment {value.id} is not ready")
        return self._target(self._client.instance.from_environment(value))

    def publish_command(
        self,
        target: PublicationTarget
        | OdooInstance
        | DevelopmentEnvironment
        | ProjectConfig
        | Path
        | str,
        *,
        settings: PublicationSettings | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[PublicationResult]:
        selected = self._target(target)
        loaded = settings or PublicationSettings.load()
        if not selected.local_endpoint.strip():
            raise PublicationError("runtime endpoint is missing")
        url = external_url(selected, loaded)
        route = _Route(
            owner_kind=selected.owner_kind,
            owner_id=selected.owner_id,
            project_id=selected.project_id,
            environment_id=selected.environment_id,
            local_endpoint=selected.local_endpoint,
            external_url=url,
            route_identity=_route_identity(selected, url),
        )
        return self._mutation_command(
            selected, loaded, route=route, remove=False, executor=executor
        )

    def publish(
        self,
        target: PublicationTarget
        | OdooInstance
        | DevelopmentEnvironment
        | ProjectConfig
        | Path
        | str,
        *,
        settings: PublicationSettings | None = None,
    ) -> PublicationResult:
        return self.publish_command(target, settings=settings).run()

    def unpublish_command(
        self,
        target: PublicationTarget
        | OdooInstance
        | DevelopmentEnvironment
        | ProjectConfig
        | Path
        | str
        | tuple[str, str],
        *,
        settings: PublicationSettings | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[PublicationResult]:
        if isinstance(target, tuple):
            kind, owner_id = target
            selected = PublicationTarget(
                owner_kind=cast("Literal['project', 'environment']", kind),
                owner_id=owner_id,
                local_endpoint="",
                project_id=owner_id if kind == "project" else None,
                environment_id=owner_id if kind == "environment" else None,
            )
        else:
            selected = self._target(target)
        loaded = settings or PublicationSettings.load()
        return self._mutation_command(selected, loaded, route=None, remove=True, executor=executor)

    def unpublish(
        self,
        target: PublicationTarget
        | OdooInstance
        | DevelopmentEnvironment
        | ProjectConfig
        | Path
        | str
        | tuple[str, str],
        *,
        settings: PublicationSettings | None = None,
    ) -> PublicationResult:
        return self.unpublish_command(target, settings=settings).run()

    def unpublish_if_configured(
        self,
        target: tuple[str, str],
        *,
        executor: ProcessExecutor | None = None,
    ) -> PublicationResult | None:
        """Remove a route when publication is configured; otherwise do nothing.

        Existing local-only installations have no publication settings.  They
        must retain their historical environment-removal behavior, while a
        configured route backend must be proven clean before removal proceeds.
        """
        if not get_publication_config_path().is_file():
            return None
        return self.unpublish_command(target, executor=executor).run()

    def _mutation_command(  # noqa: C901
        self,
        target: PublicationTarget,
        settings: PublicationSettings,
        *,
        route: _Route | None,
        remove: bool,
        executor: ProcessExecutor | None,
    ) -> Command[PublicationResult]:
        action_id = "publication.unpublish" if remove else "publication.publish"
        validation_id = f"{action_id}.caddy.validate"
        replacement_id = f"{action_id}.route.replace"
        reload_id = f"{action_id}.caddy.reload"
        restore_id = f"{action_id}.route.restore"
        restore_reload_id = f"{action_id}.caddy.reload.restore"
        candidate_path = settings.owned_route_file.with_name(
            f".{settings.owned_route_file.name}.candidate"
        )
        steps: tuple[PreparedStep | PreparedAction, ...] = (
            PreparedAction(
                step_id=action_id,
                action=action_id,
                description="Reconcile one OdCLI-owned Caddy route file",
                mutating=True,
            ),
            PreparedStep(
                step_id=validation_id,
                argv=(
                    settings.caddy_executable,
                    "validate",
                    "--config",
                    str(candidate_path),
                    "--adapter",
                    "caddyfile",
                ),
                timeout=30.0,
                read_only=True,
            ),
            PreparedAction(
                step_id=replacement_id,
                action="replace-owned-route-file",
                description="Atomically replace the OdCLI-owned Caddy route file",
                mutating=True,
            ),
            PreparedStep(
                step_id=reload_id,
                argv=(
                    settings.caddy_executable,
                    "reload",
                    "--config",
                    str(settings.owned_route_file),
                    "--adapter",
                    "caddyfile",
                    "--address",
                    settings.caddy_control_endpoint,
                ),
                timeout=30.0,
                mutating=True,
            ),
            PreparedAction(
                step_id=restore_id,
                action="restore-owned-route-file",
                description="Restore the prior owned route bytes after reload failure",
                mutating=True,
            ),
            PreparedStep(
                step_id=restore_reload_id,
                argv=(
                    settings.caddy_executable,
                    "reload",
                    "--config",
                    str(settings.owned_route_file),
                    "--adapter",
                    "caddyfile",
                    "--address",
                    settings.caddy_control_endpoint,
                ),
                timeout=30.0,
                mutating=True,
            ),
        )

        def run(context: RunContext[PublicationResult]) -> PublicationResult:
            _assert_mutation_target(target, remove=remove)
            context.action(action_id)
            path = settings.owned_route_file
            with exclusive_lock(publication_lock_path()):
                prior = path.read_bytes() if path.exists() else b""
                current = _decode_routes(prior)
                matching = next(
                    (
                        item
                        for item in current
                        if item.owner_kind == target.owner_kind and item.owner_id == target.owner_id
                    ),
                    None,
                )
                if remove:
                    updated = [item for item in current if item is not matching]
                    if matching is None:
                        context.skip(validation_id)
                        context.skip(replacement_id)
                        context.skip(reload_id)
                        context.skip(restore_id)
                        context.skip(restore_reload_id)
                        context.complete_action(action_id)
                        return _result(target, status="already_absent", route=None)
                else:
                    assert route is not None
                    updated = [
                        item
                        for item in current
                        if not (
                            item.owner_kind == route.owner_kind and item.owner_id == route.owner_id
                        )
                    ]
                    updated.append(route)
                candidate = _render_routes(updated, settings)
                temp_path = path.with_name(f".{path.name}.candidate")
                _atomic_write(temp_path, candidate)
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
                        # The recovery reload is planned and consumed through
                        # the same process boundary; its result is deliberately
                        # secondary to the original bounded failure.
                        if context.planned(restore_reload_id) and not context.consumed(
                            restore_reload_id
                        ):
                            context.process(restore_reload_id)
                    else:
                        if not context.consumed(restore_id):
                            context.skip(restore_id)
                        if not context.consumed(restore_reload_id):
                            context.skip(restore_reload_id)
                    if isinstance(exc, PublicationError):
                        raise
                    raise PublicationError(
                        sanitize_last_error(str(exc)) or "publication failed"
                    ) from exc
                finally:
                    temp_path.unlink(missing_ok=True)
                context.skip(restore_id)
                context.skip(restore_reload_id)
                context.complete_action(action_id)
                if remove:
                    return _result(target, status="unpublished", route=matching)
                status: Literal["published", "already_published"] = (
                    "already_published" if matching == route else "published"
                )
                return _result(target, status=status, route=route)

        plan_steps = tuple(step.public_projection() for step in steps)
        return Command.create(
            ExecutionPlan(steps=plan_steps),
            run,
            steps,
            executor=executor or SubprocessExecutor(),
        )


def publication_settings(path: str | Path | None = None) -> PublicationSettings:
    return PublicationSettings.load(path)


def publish_command(
    client: OdooClient,
    target: PublicationTarget | OdooInstance | DevelopmentEnvironment | ProjectConfig | Path | str,
    *,
    settings: PublicationSettings | None = None,
    executor: ProcessExecutor | None = None,
) -> Command[PublicationResult]:
    """Build one inspectable publish operation through the client resource."""
    return client.publication.publish_command(target, settings=settings, executor=executor)


def unpublish_command(
    client: OdooClient,
    target: PublicationTarget
    | OdooInstance
    | DevelopmentEnvironment
    | ProjectConfig
    | Path
    | str
    | tuple[str, str],
    *,
    settings: PublicationSettings | None = None,
    executor: ProcessExecutor | None = None,
) -> Command[PublicationResult]:
    """Build one inspectable idempotent unpublish operation."""
    return client.publication.unpublish_command(target, settings=settings, executor=executor)


__all__ = [
    "PublicationResource",
    "PublicationResult",
    "PublicationSettings",
    "PublicationTarget",
    "external_url",
    "publication_settings",
    "publish_command",
    "render_panel_route",
    "unpublish_command",
]
