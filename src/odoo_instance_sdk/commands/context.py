"""Typed Click context adapters used by CLI command callbacks."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from odoo_instance_sdk.exceptions import EnvironmentResolutionError
from odoo_instance_sdk.models import DevelopmentEnvironment, EnvironmentState

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.resources.instance import OdooInstance


@dataclass(slots=True)
class CliContext:
    """Values selected by the root command and their resolution provenance."""

    project: str | None = None
    env: str | None = None
    project_source: str = "null"
    environment_source: str = "null"
    resolved_project: Path | None = None
    resolved_environment: DevelopmentEnvironment | None = None


pass_cli_context = click.make_pass_decorator(CliContext, ensure=True)


def resolve_project_path(cli_context: CliContext) -> Path:
    """Resolve the project selector while recording its source."""
    from odoo_instance_sdk.internal import context as _resolution

    raw = cli_context.project
    project = _resolution.resolve_project(Path(raw) if raw is not None else None)
    if raw is not None:
        cli_context.project_source = "explicit"
    else:
        cwd = Path.cwd()
        cli_context.project_source = (
            "cwd"
            if _resolution._find_nearest_manifest(cwd, None) is not None
            else "worktree"
            if _resolution._project_from_registered_worktree(cwd) is not None
            else "null"
        )
    if hasattr(project, "repository_root"):
        repository_root = project.repository_root
        assert repository_root is not None
    else:
        repository_root = Path(project)
    cli_context.resolved_project = repository_root
    return repository_root


def resolve_environment(
    client: OdooClient,
    explicit: str | None,
    *,
    cwd: Path | None = None,
    cli_context: CliContext | None = None,
) -> DevelopmentEnvironment:
    """Resolve an environment from typed selectors and an optional cwd."""
    from odoo_instance_sdk.internal import context as _resolution

    environment = _resolution.resolve_environment(client, explicit, cwd=cwd)
    if cli_context is not None:
        cli_context.resolved_environment = environment
        cli_context.environment_source = "explicit" if explicit is not None else "cwd"
    return environment


def _check_port_free(env_obj: DevelopmentEnvironment) -> bool:
    from odoo_instance_sdk.internal import context as _resolution

    return _resolution._check_port_free(env_obj)


def ready_instance(
    cli_context: CliContext,
) -> tuple[OdooClient, DevelopmentEnvironment, OdooInstance]:
    """Resolve a ready environment from typed CLI context values."""
    from odoo_instance_sdk.internal import context as _resolution

    client = _client_class()(config=_client_config_class()(executable="odoo"))
    project_root = (
        resolve_project_path(cli_context).resolve() if cli_context.project is not None else None
    )
    env_obj = resolve_environment(
        client,
        cli_context.env,
        cwd=Path.cwd(),
        cli_context=cli_context,
    )
    if project_root is not None and Path(env_obj.repository_root).resolve() != project_root:
        raise EnvironmentResolutionError(
            f"Environment {env_obj.name} ({env_obj.id}) does not belong to project {project_root}"
        )
    if env_obj.state != EnvironmentState.READY:
        raise RuntimeError(
            f"Environment {env_obj.name} ({env_obj.id}) is not ready (state={env_obj.state})"
        )
    _resolution._verify_env_runtime(env_obj)
    instance = client.instance.from_environment(env_obj)
    return client, env_obj, instance


def _client_class() -> type[OdooClient]:
    return cast("type[OdooClient]", getattr(sys.modules[__name__], "OdooClient"))


def _client_config_class() -> type[OdooClientConfig]:
    return cast("type[OdooClientConfig]", getattr(sys.modules[__name__], "OdooClientConfig"))


def __getattr__(name: str) -> type[OdooClient | OdooClientConfig]:
    """Keep legacy patch points lazy for operation-only dependencies."""
    if name == "OdooClient":
        from odoo_instance_sdk.client import OdooClient

        globals()[name] = OdooClient
        return OdooClient
    if name == "OdooClientConfig":
        from odoo_instance_sdk.config import OdooClientConfig

        globals()[name] = OdooClientConfig
        return OdooClientConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CliContext",
    "pass_cli_context",
    "ready_instance",
    "resolve_environment",
    "resolve_project_path",
]
