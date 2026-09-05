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
from odoo_instance_sdk.internal.repo_key import git_common_dir, repo_key
from odoo_instance_sdk.models import DevelopmentEnvironment, EnvironmentState, StartConfig
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.client import OdooClient
    from odoo_instance_sdk.config import OdooClientConfig
    from odoo_instance_sdk.resources.instance import OdooInstance


ContextProvenance = Literal["explicit", "worktree", "cwd"]
RuntimeSource = DevelopmentEnvironment | ProjectConfig
OwnerKind = Literal["environment", "project"]
BaseProvenance = Literal["environment", "project"]


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
class RuntimeView:
    """Owner-neutral runtime inputs shared by project-capable commands."""

    owner_kind: OwnerKind
    project_id: str
    environment_id: str | None
    environment_name: str | None
    repository_root: Path
    root: Path
    start_config: StartConfig
    command_prefix: tuple[str, ...]
    python_path: Path
    database: str | None
    http_interface: str
    http_port: int
    base_ref: str | None
    base_provenance: BaseProvenance

    @property
    def http_url(self) -> str:
        return f"http://{self.http_interface}:{self.http_port}"


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

    @property
    def runtime(self) -> RuntimeView:
        """Return the single owner-neutral input for project-capable commands."""
        if isinstance(self.source, ProjectConfig) and self.source.python is None:
            raise EnvironmentResolutionError(
                "This command requires a development environment; initialize the project first"
            )
        config = self.instance.config
        candidate_start_config = getattr(config, "start_config", None)
        if isinstance(candidate_start_config, StartConfig):
            start_config = candidate_start_config
        else:
            interface = getattr(self.source, "http_interface", "127.0.0.1")
            port = getattr(self.source, "http_port", 8069)
            start_config = StartConfig(
                http_interface=str(interface),
                http_port=port if isinstance(port, int) else 8069,
            )
        if start_config is None:
            raise RuntimeError("resolved instance has no Odoo start configuration")

        root = self.worktree_path()
        raw_command_prefix = getattr(config, "command_prefix", None)
        command_prefix = (
            tuple(str(item) for item in raw_command_prefix)
            if isinstance(raw_command_prefix, (tuple, list))
            else None
        )
        deferred_runtime = getattr(config, "deferred_runtime", None)
        if command_prefix is None and deferred_runtime is not None:
            command_prefix = tuple(deferred_runtime.command_prefix())
        if command_prefix is None:
            executable = getattr(getattr(self.client, "config", None), "executable", "odoo")
            command_prefix = (str(executable),)

        database = start_config.db_name
        configured_databases = getattr(config, "configured_database_names", ())
        if (
            not database
            and isinstance(configured_databases, (tuple, list))
            and configured_databases
        ):
            database = str(configured_databases[0])

        if isinstance(self.source, ProjectConfig):
            repository_root = self.source.repository_root.resolve()
            project_id = f"project_{repo_key(repository_root, git_common_dir(repository_root))}"
            return RuntimeView(
                owner_kind="project",
                project_id=project_id,
                environment_id=None,
                environment_name=None,
                repository_root=repository_root,
                root=root,
                start_config=start_config,
                command_prefix=command_prefix,
                python_path=self.python_path(),
                database=database,
                http_interface=start_config.http_interface,
                http_port=start_config.http_port,
                base_ref=self.source.default_base_ref,
                base_provenance="project",
            )

        environment = self.source
        repository_root = Path(getattr(environment, "repository_root", root)).resolve()
        environment_git_common = Path(
            getattr(environment, "git_common_dir", repository_root / ".git")
        )
        project_id = f"project_{repo_key(repository_root, environment_git_common)}"
        if not database:
            database = getattr(environment, "target_db_name", None) or getattr(
                environment, "source_db_name", None
            )
        return RuntimeView(
            owner_kind="environment",
            project_id=project_id,
            environment_id=str(environment.id),
            environment_name=str(environment.name),
            repository_root=repository_root,
            root=root,
            start_config=start_config,
            command_prefix=command_prefix,
            python_path=self.python_path(),
            database=database,
            http_interface=start_config.http_interface,
            http_port=start_config.http_port,
            base_ref=getattr(environment, "base_ref", None),
            base_provenance="environment",
        )

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
        path_value = getattr(self.source, "python_environment_path", None)
        if path_value is None:
            config_prefix = getattr(self.instance.config, "command_prefix", None)
            if isinstance(config_prefix, (tuple, list)) and config_prefix:
                return Path(str(config_prefix[0]))
            executable = getattr(getattr(self.client, "config", None), "executable", "odoo")
            return Path(str(executable))
        path = Path(path_value)
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
    "BaseProvenance",
    "CliContext",
    "OwnerKind",
    "ResolvedContext",
    "RuntimeView",
    "environment_provenance",
    "pass_cli_context",
    "project_provenance",
    "ready_instance",
    "resolve_environment",
    "resolve_project_path",
]
