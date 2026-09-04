"""Typed Click context adapters used by CLI command callbacks."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    import click
else:
    import rich_click as click

from odoo_instance_sdk.exceptions import EnvironmentResolutionError
from odoo_instance_sdk.internal.project_runtime import resolve_project_runtime
from odoo_instance_sdk.models import DevelopmentEnvironment, EnvironmentState
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.resources.instance import OdooInstance


ContextProvenance = Literal["explicit", "worktree", "cwd"]
RuntimeSource = DevelopmentEnvironment | ProjectConfig


@dataclass(slots=True)
class CliContext:
    """Selectors supplied to the root command.

    Resolution results deliberately do not live here. Keeping the Click
    object as input-only state prevents a command from observing stale
    provenance left by an earlier resolver call.
    """

    project: str | None = None
    env: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedContext:
    """The one resolved runtime context consumed by instance commands."""

    client: OdooClient
    instance: OdooInstance
    source: RuntimeSource
    provenance: ContextProvenance

    @property
    def is_environment(self) -> bool:
        return not isinstance(self.source, ProjectConfig)

    @property
    def project_root(self) -> Path:
        if isinstance(self.source, ProjectConfig):
            return self.source.repository_root
        return Path(self.source.repository_root)

    @property
    def output_provenance(self) -> dict[str, str]:
        """Preserve the CLI envelope's two-field provenance projection."""
        if self.is_environment:
            return {
                "project_source": "worktree" if self.provenance == "worktree" else "null",
                "environment_source": "cwd" if self.provenance == "worktree" else "explicit",
            }
        return {
            "project_source": (
                "explicit"
                if self.provenance == "explicit"
                else "worktree"
                if self.provenance == "worktree"
                else "cwd"
            ),
            "environment_source": "null",
        }

    def require_environment(self) -> DevelopmentEnvironment:
        if isinstance(self.source, ProjectConfig):
            raise EnvironmentResolutionError(
                "This command requires a development environment; pass --env or cd into its worktree"
            )
        return self.source

    def worktree_path(self) -> Path:
        if isinstance(self.source, ProjectConfig):
            return self.source.repository_root
        return Path(self.source.worktree_path)

    def python_path(self) -> Path:
        if isinstance(self.source, ProjectConfig):
            return resolve_project_runtime(
                self.source.repository_root, self.source.python, field="python"
            )
        path = Path(self.source.python_environment_path)
        return path / "bin" / "python" if path.is_dir() else path

    def instance_address(self) -> tuple[str, int]:
        if isinstance(self.source, DevelopmentEnvironment):
            return self.source.http_interface, self.source.http_port
        start_config = self.instance.config.start_config
        if start_config is None:
            raise RuntimeError("resolved project instance has no Odoo start configuration")
        return start_config.http_interface, start_config.http_port

    def check_port_free(self) -> bool:
        from odoo_instance_sdk.internal import context as _resolution

        if self.is_environment:
            return _resolution._check_port_free(cast("DevelopmentEnvironment", self.source))
        start_config = self.instance.config.start_config
        if start_config is None:
            raise RuntimeError("resolved project instance has no Odoo start configuration")
        from odoo_instance_sdk.internal.address import AddressState, probe_address

        return (
            probe_address(start_config.http_interface, start_config.http_port) is AddressState.FREE
        )


pass_cli_context = click.make_pass_decorator(CliContext, ensure=True)


def project_provenance(cli_context: CliContext) -> Literal["explicit", "worktree", "cwd", "null"]:
    """Return project provenance without mutating the Click context."""
    from odoo_instance_sdk.internal import context as _resolution

    if cli_context.project is not None:
        return "explicit"
    cwd = Path.cwd()
    if _resolution._project_from_registered_worktree(cwd) is not None:
        return "worktree"
    if _resolution._find_nearest_manifest(cwd, None) is not None:
        return "cwd"
    return "null"


def environment_provenance(cli_context: CliContext) -> Literal["explicit", "cwd"]:
    """Return the selector provenance used by legacy environment commands."""
    return "explicit" if cli_context.env is not None else "cwd"


def resolve_project_path(cli_context: CliContext) -> Path:
    """Resolve the explicit or nearest initialized project."""
    from odoo_instance_sdk.internal import context as _resolution

    raw = cli_context.project
    project = _resolution.resolve_project(Path(raw) if raw is not None else None, cwd=Path.cwd())
    if isinstance(project, ProjectConfig):
        return project.repository_root
    return Path(project)


def resolve_environment(
    client: OdooClient,
    explicit: str | None,
    *,
    cwd: Path | None = None,
) -> DevelopmentEnvironment:
    """Resolve an environment from typed selectors and an optional cwd."""
    from odoo_instance_sdk.internal import context as _resolution

    return _resolution.resolve_environment(client, explicit, cwd=cwd)


def ready_instance(cli_context: CliContext) -> ResolvedContext:
    """Resolve one runtime source and construct its immutable context.

    Precedence is intentionally visible here: explicit ``--env``, an exact
    registered worktree, explicit ``--project``, then the nearest project.
    """
    from odoo_instance_sdk.internal import context as _resolution

    client = _client_class()(config=_client_config_class()(executable="odoo"))
    try:
        env_obj = resolve_environment(
            client,
            cli_context.env,
            cwd=Path.cwd(),
        )
    except EnvironmentResolutionError:
        if cli_context.env is not None:
            raise
        project = _project_for_context(cli_context)
        instance = client.instance.from_project(project)
        provenance: ContextProvenance = "explicit"
        if cli_context.project is None:
            provenance = (
                "worktree"
                if _resolution._project_from_registered_worktree(Path.cwd()) is not None
                else "cwd"
            )
        return ResolvedContext(
            client=client,
            instance=instance,
            source=project,
            provenance=provenance,
        )

    if env_obj.state != EnvironmentState.READY:
        raise RuntimeError(
            f"Environment {env_obj.name} ({env_obj.id}) is not ready (state={env_obj.state})"
        )
    _resolution._verify_env_runtime(env_obj)
    instance = client.instance.from_environment(env_obj)
    return ResolvedContext(
        client=client,
        instance=instance,
        source=env_obj,
        provenance="explicit" if cli_context.env is not None else "worktree",
    )


def _project_for_context(cli_context: CliContext) -> ProjectConfig:
    """Load the project only after environment/worktree resolution failed."""
    from odoo_instance_sdk.internal import context as _resolution

    if cli_context.project is not None:
        return ProjectConfig.load(resolve_project_path(cli_context))
    resolved = _resolution.resolve_project(None, cwd=Path.cwd())
    if isinstance(resolved, ProjectConfig):
        return resolved
    return ProjectConfig.load(Path(resolved))


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
    "ResolvedContext",
    "environment_provenance",
    "pass_cli_context",
    "project_provenance",
    "ready_instance",
    "resolve_environment",
    "resolve_project_path",
]
