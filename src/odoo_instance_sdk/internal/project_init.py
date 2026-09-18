"""Project initialization helpers shared by the public SDK and CLI."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import InstanceConfigurationError
from odoo_instance_sdk.internal.database_preparation import _planned_project_identity
from odoo_instance_sdk.internal.generated_config import (
    generate_config,
    project_generated_config_path,
    render_config,
)
from odoo_instance_sdk.internal.project_runtime import resolve_project_http_port
from odoo_instance_sdk.models import StartConfig
from odoo_instance_sdk.project import ProjectConfig

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue


def _resolve_source_config_path(root: Path, source: Path | None) -> Path | None:
    """Resolve the optional project source config path for init and repair."""
    source_path = (
        (root / source).resolve() if source is not None and not source.is_absolute() else source
    )
    if source_path is None:
        candidate = root / "odoo.conf"
        return candidate if candidate.is_file() else None
    return source_path if source_path.is_file() else None


def manifest_dict(
    config: ProjectConfig, *, postgres_allocated: bool = False
) -> dict[str, JsonValue]:
    """Project one init manifest document for preview and machine output."""
    postgres: dict[str, JsonValue] | None = None
    if config.postgres is not None:
        postgres = {
            "mode": config.postgres.mode,
            "image": config.postgres.image,
            "port": config.postgres.port,
            "user": config.postgres.user,
            "allocated_port": postgres_allocated,
        }
    test_instance: dict[str, JsonValue] | None = None
    if config.test_instance is not None:
        test_instance = {
            "base_url": config.test_instance.base_url,
            "database": config.test_instance.database,
            "git_branch": config.test_instance.git_branch,
        }
    return {
        "odoo_bin": str(config.odoo_bin) if config.odoo_bin else None,
        "python": str(config.python) if config.python else None,
        "source_config": str(config.source_config) if config.source_config else None,
        "default_source_database": config.default_source_database,
        "default_base_ref": config.default_base_ref,
        "ticket_link_enabled": config.ticket_link_enabled is True,
        "ticket_base_url": config.ticket_base_url,
        "refresh_after_hours": config.refresh_after_hours,
        "test_instance": test_instance,
        "preferred_http_port": config.preferred_http_port,
        "requirements": list(config.requirements),
        "default_run_args": list(config.default_run_args),
        "runtime_cwd": str(config.runtime_cwd) if config.runtime_cwd else None,
        "postgres": postgres,
    }


def write_project_generated_config(project_path: Path, config: ProjectConfig) -> None:
    """Bind a Compose project config to its existing private cluster secret."""
    root = project_path.resolve()
    destination = project_generated_config_path(root)
    from odoo_instance_sdk.internal.proc import active_context

    validate_generated_config_target(
        destination,
        project_root=root,
        check_tracking=active_context() is None,
    )
    source_path = _resolve_source_config_path(root, config.source_config)
    if config.source_config is not None and source_path is None:
        raise InstanceConfigurationError("local source config is missing")

    from odoo_instance_sdk.internal.postgres_compose import ensure_password_file
    from odoo_instance_sdk.resources.postgres import PostgresCluster

    cluster = PostgresCluster.from_project(root)
    password = ensure_password_file(cluster.password_file)
    source_start = (
        StartConfig.from_odoo_config(source_path) if source_path is not None else StartConfig()
    )
    postgres = config.postgres
    assert postgres is not None
    generate_config(
        source_path,
        destination,
        repo_root=root,
        worktree=root,
        http_interface=source_start.http_interface,
        http_port=resolve_project_http_port(config.preferred_http_port, source_start.http_port),
        db_name=config.default_source_database or source_start.db_name or "",
        db_host=cluster.endpoint_host,
        db_port=cluster.endpoint_port,
        db_user=postgres.user or "odoo",
        db_password=password,
    )


def validate_generated_config_target(  # noqa: C901
    path: Path,
    *,
    project_root: Path | None = None,
    check_tracking: bool = True,
) -> None:
    """Reject unsafe targets before any generated-config or secret write."""
    root = (project_root or path.parent.parent).resolve()
    expected = root / ".odcli" / "odoo.conf"
    if path.absolute() != expected:
        raise InstanceConfigurationError(
            f"generated config target is outside the project-owned path: {path}"
        )
    relative = path.relative_to(root)
    current = root
    for component in relative.parts[:-1]:
        current /= component
        try:
            parent = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            raise InstanceConfigurationError(
                f"generated config parent must be a project-owned directory: {current}"
            )
    try:
        target = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or not path.is_file():
        raise InstanceConfigurationError(
            f"generated config target must be a regular file, not a symlink or directory: {path}"
        )
    if target.st_uid != os.getuid():
        raise InstanceConfigurationError(
            f"generated config is not owned by the current user: {path}"
        )
    if check_tracking:
        from odoo_instance_sdk.internal.git_worktree import GitError, is_tracked_path

        try:
            if is_tracked_path(path):
                raise InstanceConfigurationError(
                    "project-owned runtime config is tracked; refusing secret write: .odcli/odoo.conf"
                )
        except GitError as exc:
            raise InstanceConfigurationError(
                "unable to verify project-owned runtime config tracking; refusing secret write"
            ) from exc


def generated_config_needs_repair(project_path: Path, config: ProjectConfig) -> bool:
    """Compare generated bytes to current inputs without creating anything."""
    if config.postgres is None or config.postgres.mode != "compose":
        return False
    destination = project_generated_config_path(project_path)
    try:
        if destination.is_symlink() or not destination.is_file():
            return True
        if destination.stat().st_mode & 0o777 != 0o600:
            return True
        from odoo_instance_sdk.resources.postgres import PostgresCluster

        cluster = PostgresCluster.from_project(project_path)
        if not cluster.password_file.is_file():
            return True
        password = cluster.password_file.read_text(encoding="utf-8").strip()
        source_path = _resolve_source_config_path(project_path, config.source_config)
        if config.source_config is not None and source_path is None:
            return True
        source_start = (
            StartConfig.from_odoo_config(source_path) if source_path is not None else StartConfig()
        )
        expected = render_config(
            source_path,
            destination,
            repo_root=project_path,
            worktree=project_path,
            http_interface=source_start.http_interface,
            http_port=resolve_project_http_port(config.preferred_http_port, source_start.http_port),
            db_name=config.default_source_database or source_start.db_name or "",
            db_host=cluster.endpoint_host,
            db_port=cluster.endpoint_port,
            db_user=config.postgres.user or "odoo",
            db_password=password,
        )
        return destination.read_text(encoding="utf-8") != expected
    except (OSError, UnicodeError, InstanceConfigurationError, ValueError):
        return True


def register_initialized_project(project_path: Path) -> None:
    """Idempotently register a project after its valid manifest is available."""
    root, common, identity = _planned_project_identity(project_path)
    project_id = f"project_{identity}"
    from odoo_instance_sdk.internal.paths import get_catalog_path
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog(db_path=get_catalog_path())
    try:
        catalog._register_project(project_id, root, common)
    finally:
        catalog.close()
