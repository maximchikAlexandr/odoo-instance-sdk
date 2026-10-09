"""Safe, single-file Caddy publication for project and environment runtimes.

Publication is intentionally a small resource.  The route file is the only
publication state; the catalog and project manifests remain authorities for
runtime and database identity.
"""

from __future__ import annotations

import ipaddress
import re
import stat
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlsplit

import msgspec

from odoo_instance_sdk.exceptions import ConfigError, PublicationError
from odoo_instance_sdk.execution import Command, ExecutionPlan
from odoo_instance_sdk.internal.paths import get_config_root, get_publication_config_path
from odoo_instance_sdk.internal.proc import (
    PreparedAction,
    PreparedStep,
    ProcessExecutor,
    RunContext,
    SubprocessExecutor,
)
from odoo_instance_sdk.internal.publication_routes import (
    PublicationRoute,
    decode_routes,
    panel_route,
    read_owned_routes,
    reconcile_route_file,
    render_panel_route,
    route_identity,
)

_Route = PublicationRoute
_decode_routes = decode_routes
_read_owned_routes = read_owned_routes

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


def _host_suffix(value: str | None) -> str:
    if not isinstance(value, str):
        raise ConfigError("publication domain_suffix must be a string")
    value = value.strip().lower().rstrip(".")
    if not value or "*" in value or any(char in value for char in "/:@"):
        raise ConfigError("publication domain_suffix must be a DNS suffix")
    labels = value.split(".")
    if any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise ConfigError("publication domain_suffix contains an invalid DNS label")
    return value


def _local_endpoint(value: str | None) -> str:
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


def _owned_path(value: str | Path | None, *, root: Path) -> Path:
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


def _host_label(value: str | None, field: str) -> str:
    if not isinstance(value, str) or not _DNS_LABEL.fullmatch(value.strip().lower()):
        raise ConfigError(f"publication {field} must be one DNS label")
    return value.strip().lower()


def _proxy_addresses(value: Sequence[str] | None) -> tuple[str, ...]:
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
        settings_path = get_publication_config_path()
        if path is not None and Path(path).expanduser().resolve(
            strict=False
        ) != settings_path.resolve(strict=False):
            raise ConfigError("publication settings must use the canonical user config path")
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


def _result(
    target: PublicationTarget,
    *,
    status: Literal["published", "already_published", "unpublished", "already_absent"],
    route: PublicationRoute | None,
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


def _publication_steps(
    settings: PublicationSettings,
    action_id: str,
    *,
    action_description: str,
    replacement_description: str,
    restore_description: str,
) -> tuple[PreparedStep | PreparedAction, ...]:
    """Build the shared Caddy validation, replacement, and recovery steps."""
    validation_id = f"{action_id}.caddy.validate"
    replacement_id = f"{action_id}.route.replace"
    reload_id = f"{action_id}.caddy.reload"
    restore_id = f"{action_id}.route.restore"
    restore_reload_id = f"{action_id}.caddy.reload.restore"
    candidate_path = settings.owned_route_file.with_name(
        f".{settings.owned_route_file.name}.candidate"
    )
    reload_argv = (
        settings.caddy_executable,
        "reload",
        "--config",
        str(settings.owned_route_file),
        "--adapter",
        "caddyfile",
        "--address",
        settings.caddy_control_endpoint,
    )
    return (
        PreparedAction(
            step_id=action_id,
            action=action_id,
            description=action_description,
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
            description=replacement_description,
            mutating=True,
        ),
        PreparedStep(
            step_id=reload_id,
            argv=reload_argv,
            timeout=30.0,
            mutating=True,
        ),
        PreparedAction(
            step_id=restore_id,
            action="restore-owned-route-file",
            description=restore_description,
            mutating=True,
        ),
        PreparedStep(
            step_id=restore_reload_id,
            argv=reload_argv,
            timeout=30.0,
            mutating=True,
        ),
    )


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
        raise PublicationError("runtime readiness must be proven before publication")
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
        route = PublicationRoute(
            owner_kind=selected.owner_kind,
            owner_id=selected.owner_id,
            project_id=selected.project_id,
            environment_id=selected.environment_id,
            local_endpoint=selected.local_endpoint,
            external_url=url,
            route_identity=route_identity(selected.owner_kind, selected.owner_id, url),
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

    def monitor_route_command(
        self,
        local_endpoint: str,
        *,
        settings: PublicationSettings | None = None,
        executor: ProcessExecutor | None = None,
    ) -> Command[str]:
        """Register the monitor endpoint in the owned Caddy route file."""
        loaded = settings or PublicationSettings.load()
        route = panel_route(local_endpoint, loaded)
        action_id = "publication.monitor"
        steps = _publication_steps(
            loaded,
            action_id,
            action_description="Reconcile the OdCLI monitor Caddy route",
            replacement_description="Atomically replace the OdCLI-owned route file",
            restore_description="Restore the prior route bytes after reload failure",
        )

        def run(context: RunContext[str]) -> str:
            reconcile_route_file(
                context,
                loaded,
                action_id=action_id,
                owner_kind="panel",
                owner_id="monitor",
                route=route,
                remove=False,
            )
            return route.external_url

        plan_steps = tuple(step.public_projection() for step in steps)
        return Command.create(
            ExecutionPlan(steps=plan_steps),
            run,
            steps,
            executor=executor or SubprocessExecutor(),
        )

    def _mutation_command(
        self,
        target: PublicationTarget,
        settings: PublicationSettings,
        *,
        route: PublicationRoute | None,
        remove: bool,
        executor: ProcessExecutor | None,
    ) -> Command[PublicationResult]:
        action_id = "publication.unpublish" if remove else "publication.publish"
        steps = _publication_steps(
            settings,
            action_id,
            action_description="Reconcile one OdCLI-owned Caddy route file",
            replacement_description="Atomically replace the OdCLI-owned Caddy route file",
            restore_description="Restore the prior owned route bytes after reload failure",
        )

        def run(context: RunContext[PublicationResult]) -> PublicationResult:
            _assert_mutation_target(target, remove=remove)
            matching = reconcile_route_file(
                context,
                settings,
                action_id=action_id,
                owner_kind=target.owner_kind,
                owner_id=target.owner_id,
                route=route,
                remove=remove,
            )
            if remove:
                return _result(
                    target,
                    status="already_absent" if matching is None else "unpublished",
                    route=matching,
                )
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


def monitor_route_command(
    local_endpoint: str,
    *,
    settings: PublicationSettings | None = None,
    executor: ProcessExecutor | None = None,
) -> Command[str]:
    """Build the inspectable monitor route reconciliation operation."""
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig

    client = OdooClient(config=OdooClientConfig(executable="odoo"))
    return client.publication.monitor_route_command(
        local_endpoint, settings=settings, executor=executor
    )


__all__ = [
    "PublicationResource",
    "PublicationResult",
    "PublicationSettings",
    "PublicationTarget",
    "external_url",
    "monitor_route_command",
    "publication_settings",
    "publish_command",
    "render_panel_route",
    "unpublish_command",
]
