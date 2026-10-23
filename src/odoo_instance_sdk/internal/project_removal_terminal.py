"""Read-only observations for environment ownership and terminal purges."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.internal.project_removal import (
    ProjectRegistration,
    RemovalRuntimeSource,
)
from odoo_instance_sdk.internal.resource_inventory import (
    ActiveUse,
    EnvironmentResourceSource,
    OwnershipConfidence,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def _environment_owner_clause(
    registration: ProjectRegistration,
    *,
    alias: str = "e",
) -> tuple[str, tuple[str, ...]]:
    """Select current and legacy rows owned by one captured registration."""
    if registration.git_common_dir is None:
        return f"{alias}.project_id = ?", (registration.project_id,)
    return (
        (
            f"({alias}.project_id = ? OR ({alias}.project_id IS NULL "
            f"AND {alias}.repository_root = ? AND {alias}.git_common_dir = ?))"
        ),
        (
            registration.project_id,
            str(registration.repository_root),
            str(registration.git_common_dir),
        ),
    )


def _environment_sources(
    catalog: BackupCatalog, registration: ProjectRegistration
) -> tuple[EnvironmentResourceSource, ...]:
    owner_clause, owner_params = _environment_owner_clause(registration)
    rows = catalog._conn.execute(
        f"SELECT * FROM environments e WHERE {owner_clause} ORDER BY id", owner_params
    ).fetchall()
    return tuple(
        EnvironmentResourceSource(
            environment_id=str(row["id"]),
            project_id=registration.project_id,
            name=str(row["name"]),
            worktree_path=Path(str(row["worktree_path"])),
            python_environment_path=Path(str(row["python_environment_path"])),
            python_environment_owned=bool(row["python_environment_owned"]),
            state=str(row["state"]),
            backup_id=str(row["backup_id"]) if row["backup_id"] else None,
            last_error=str(row["last_error"]) if row["last_error"] else None,
        )
        for row in rows
    )


def _project_runtime(
    catalog: BackupCatalog, registration: ProjectRegistration
) -> tuple[RemovalRuntimeSource, ...]:
    project_id = registration.project_id
    sources: list[RemovalRuntimeSource] = []
    if catalog.get_runtime("project", project_id) is not None:
        sources.append(
            RemovalRuntimeSource(
                project_id=project_id,
                identity=f"runtime:project:{project_id}",
                active_use=ActiveUse.ACTIVE,
                ownership=OwnershipConfidence.PROVEN,
                stale=False,
            )
        )
    owner_clause, owner_params = _environment_owner_clause(registration)
    for row in catalog._conn.execute(
        f"SELECT e.id FROM environments e WHERE {owner_clause} ORDER BY e.id", owner_params
    ):
        environment_id = str(row["id"])
        if catalog.get_runtime("environment", environment_id) is not None:
            sources.append(
                RemovalRuntimeSource(
                    project_id=project_id,
                    identity=f"runtime:environment:{environment_id}",
                    target="environment runtime",
                    active_use=ActiveUse.ACTIVE,
                    ownership=OwnershipConfidence.PROVEN,
                    stale=False,
                )
            )
    return tuple(sources)


def _forgotten_project_is_inert(catalog: BackupCatalog, registration: ProjectRegistration) -> bool:
    """Treat a forgotten project as inert only while every owned resource stays absent."""
    if registration.registration_state != "forgotten":
        return False
    root = registration.repository_root
    manifest = root / ".odcli" / "project.toml"
    claim = catalog._get_postgres_cluster_for_project(
        registration.project_id,
        str(root),
        str(registration.git_common_dir),
    )
    if manifest.exists() or manifest.is_symlink() or claim is not None:
        return False
    if (
        catalog._conn.execute(
            "SELECT 1 FROM project_database_bindings WHERE project_id = ? LIMIT 1",
            (registration.project_id,),
        ).fetchone()
        is not None
    ):
        return False
    from odoo_instance_sdk.internal.project_filesystem_purge import project_owned_paths

    if project_owned_paths(root) or _project_runtime(catalog, registration):
        return False
    for environment in _environment_sources(catalog, registration):
        if environment.state != "removed":
            return False
        if environment.worktree_path.exists() or environment.worktree_path.is_symlink():
            return False
        if environment.python_environment_owned and (
            environment.python_environment_path.exists()
            or environment.python_environment_path.is_symlink()
        ):
            return False
    return True


__all__ = [
    "_forgotten_project_is_inert",
    "_environment_owner_clause",
    "_environment_sources",
    "_project_runtime",
]
