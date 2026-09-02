"""Bound database context used by PostgreSQL command resources."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import ConfigError, ProjectContextError
from odoo_instance_sdk.internal.db_name import validate_db_name

if TYPE_CHECKING:
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.resources.instance import OdooInstance
    from odoo_instance_sdk.resources.postgres import PostgresCluster


@dataclass(frozen=True, slots=True)
class DatabaseContext:
    """The connection identity inherited by one instance-bound operation."""

    database: str
    host: str | None
    port: int
    user: str | None
    password: str | None
    cluster: PostgresCluster | None = None


def resolve_database_name(
    configured: Sequence[str],
    *,
    explicit: str | None = None,
    project_default: str | None = None,
) -> str:
    """Resolve one database name without making a network or process call.

    ``configured`` is the generated environment binding.  An explicit name
    is deliberately not checked against it: it changes only the database name
    while the host, port, and user remain bound to the same cluster.
    """
    if explicit is not None:
        name = explicit.strip()
        if not name:
            raise ConfigError("database name must not be empty")
        validate_db_name(name)
        return name

    names = tuple(name.strip() for name in configured if name.strip())
    if len(names) == 1:
        validate_db_name(names[0])
        return names[0]
    if len(names) > 1:
        raise ConfigError("database identity is ambiguous; pass an explicit database name")
    default = project_default.strip() if project_default is not None else ""
    if default:
        validate_db_name(default)
        return default
    raise ConfigError(
        "database identity is missing; configure one database or pass an explicit name"
    )


def _project_default(instance: OdooInstance) -> str | None:
    cluster = getattr(instance, "_postgres_cluster", None)
    repository_root = getattr(cluster, "_repository_root", None)
    if repository_root is None:
        default_cwd = getattr(instance.config, "default_cwd", None)
        repository_root = default_cwd
    if repository_root is None:
        return None
    try:
        from odoo_instance_sdk.project import ProjectConfig

        return ProjectConfig.load(Path(repository_root).resolve()).default_source_database
    except (OSError, ConfigError, ProjectContextError):
        return None


def resolve_database_context(
    instance: OdooInstance,
    *,
    explicit: str | None = None,
    project: ProjectConfig | None = None,
) -> DatabaseContext:
    """Resolve the instance/project database while preserving cluster identity."""
    config = instance.config
    project_default = (
        project.default_source_database if project is not None else _project_default(instance)
    )
    database = resolve_database_name(
        config.configured_database_names,
        explicit=explicit,
        project_default=project_default,
    )

    cluster = getattr(instance, "_postgres_cluster", None)
    host = getattr(cluster, "endpoint_host", config.db_host)
    port = getattr(cluster, "endpoint_port", None) or config.db_port or 5432
    return DatabaseContext(
        database=database,
        host=host,
        port=port,
        user=config.db_user,
        password=config.db_password,
        cluster=cluster,
    )


__all__ = ["DatabaseContext", "resolve_database_context", "resolve_database_name"]
