"""Read-only local resource and doctor projections.

This module is deliberately a projection boundary.  It does not own a
catalogue, reconcile observations, or call any mutating SDK operation.  The
small source dataclasses make the expensive probes injectable while
``collect_resource_inventory`` joins the already captured catalogue rows.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

import msgspec

from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.sanitize import sanitize_event_message
from odoo_instance_sdk.models import StorageFootprint

if TYPE_CHECKING:
    from odoo_instance_sdk.internal.pg.inventory import (
        DatabaseInventoryItem,
        DatabaseInventoryResult,
    )
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog, BackupProjection


class ResourceType(StrEnum):
    PROJECT = "project"
    BACKUP = "backup"
    DATABASE = "database"
    ENVIRONMENT = "environment"
    WORKTREE = "worktree"
    PYTHON_ENVIRONMENT = "python_environment"
    VOLUME = "volume"
    LOG = "log"
    FILESTORE = "filestore"
    OTHER = "other"


class OwnershipConfidence(StrEnum):
    PROVEN = "proven"
    SHARED = "shared"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class ActiveUse(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class MeasurementCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ResourceInventoryItem(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One stable, sanitized local resource observation."""

    stable_identity: str
    type: ResourceType
    name: str
    relationships: tuple[str, ...] = ()
    ownership_confidence: OwnershipConfidence = OwnershipConfidence.UNKNOWN
    active_use: ActiveUse = ActiveUse.UNKNOWN
    logical_bytes: int | None = None
    logical_bytes_semantics: str | None = None
    measured_bytes: int | None = None
    measured_bytes_semantics: str | None = None
    completeness: MeasurementCompleteness = MeasurementCompleteness.UNAVAILABLE
    completeness_reason: str | None = None
    reclaimable: bool = False
    reclaimability_reason: str = "ownership or measurement is not proven"
    sanitized_path: str | None = None
    recommendation: str | None = None

    def __post_init__(self) -> None:
        if not self.stable_identity.strip():
            raise ValueError("resource stable_identity must not be empty")
        if not self.name.strip():
            raise ValueError("resource name must not be empty")
        for field in ("logical_bytes", "measured_bytes"):
            value = getattr(self, field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"resource {field} must be a non-negative integer")
        if self.sanitized_path is not None and os.path.isabs(self.sanitized_path):
            raise ValueError("resource paths must be sanitized")
        if self.reclaimable and self.ownership_confidence is not OwnershipConfidence.PROVEN:
            raise ValueError("only proven resources can be reclaimable")


class ResourceFinding(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """A typed doctor observation; creating one never changes lifecycle state."""

    code: str
    severity: Literal["warning", "error"]
    stable_identity: str
    message: str
    sanitized_path: str | None = None
    measured_bytes: int | None = None
    recommendation: str | None = None

    def __post_init__(self) -> None:
        if self.measured_bytes is not None and (
            type(self.measured_bytes) is not int or self.measured_bytes < 0
        ):
            raise ValueError("finding measured_bytes must be a non-negative integer")
        if self.sanitized_path is not None and os.path.isabs(self.sanitized_path):
            raise ValueError("finding paths must be sanitized")


class ResourceInventory(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """One deterministic read-only resource graph and its findings."""

    __odcli_structural_paths__: ClassVar[frozenset[str]] = frozenset({"complete"})

    resources: tuple[ResourceInventoryItem, ...]
    findings: tuple[ResourceFinding, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class EnvironmentResourceSource:
    """Path-bearing catalogue input kept outside the monitor API DTO."""

    environment_id: str
    project_id: str
    name: str
    worktree_path: Path
    python_environment_path: Path
    python_environment_owned: bool
    state: str
    storage: StorageFootprint | None = None
    backup_id: str | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectResourceSource:
    project_id: str
    repository_root: Path
    runtime_database: str | None = None
    database_cluster: str | None = None


@dataclass(frozen=True, slots=True)
class VolumeResourceSource:
    """Result of the existing Compose volume probe.

    ``volume_name`` is an internal input only.  The projection exposes a
    stable digest, never the Docker name, and a retained volume is never
    marked reclaimable (including when it is stopped).
    """

    project_id: str
    volume_name: str
    cluster_id: str | None
    owned: bool
    available: bool
    usage_bytes: int | None
    active: bool | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FileResourceSource:
    path: Path
    kind: Literal["log", "filestore", "owned_file", "part"]
    owner_identity: str | None = None
    ownership: OwnershipConfidence = OwnershipConfidence.UNKNOWN


def stable_resource_identity(kind: str, value: str) -> str:
    """Return a deterministic, non-path identity for local resource joins."""
    if not kind.strip() or not value.strip():
        raise ValueError("resource identity inputs must not be empty")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{digest}"


def open_catalog_read_only(path: Path) -> BackupCatalog | None:
    """Open an existing catalogue without schema creation or reconciliation."""
    if not path.is_file():
        return None
    from urllib.parse import quote

    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog

    catalog = BackupCatalog.__new__(BackupCatalog)
    catalog.db_path = path
    catalog._read_only = True
    catalog._conn = sqlite3.connect(
        f"file:{quote(str(path.resolve()), safe='/')}?mode=ro", uri=True
    )
    catalog._conn.row_factory = sqlite3.Row
    return catalog


def _path_display(path: Path | None, roots: Sequence[Path]) -> str | None:
    if path is None:
        return None
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return "<local>"
    for root in roots:
        try:
            relative = resolved.relative_to(root.resolve(strict=False))
        except (OSError, ValueError):
            continue
        return "<local>" if not str(relative) else f"<local>/{relative.as_posix()}"
    # A local maintenance projection may still point at an unregistered
    # location, but must not disclose its absolute parent directories.
    return f"<local>/{resolved.name}" if resolved.name else "<local>"


def _safe_message(message: str | None, fallback: str) -> str:
    return sanitize_event_message(message) or fallback


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


def _database_identity(cluster: str, name: str) -> str:
    return stable_resource_identity("database", f"{cluster}\x00{name}")


def _database_identity_maps(
    databases: Sequence[DatabaseInventoryItem],
) -> tuple[dict[tuple[str, str], str], dict[str, tuple[str, ...]]]:
    by_cluster_name: dict[tuple[str, str], str] = {}
    by_name: dict[str, list[str]] = {}
    for database in databases:
        cluster = str(database.cluster_id or database.cluster)
        identity = _database_identity(cluster, database.name)
        by_cluster_name[(database.cluster, database.name)] = identity
        by_name.setdefault(database.name, []).append(identity)
    return by_cluster_name, {name: tuple(dict.fromkeys(values)) for name, values in by_name.items()}


def _database_edge(
    name: str,
    *,
    cluster: str | None,
    by_cluster_name: Mapping[tuple[str, str], str],
    by_name: Mapping[str, tuple[str, ...]],
) -> str | None:
    if cluster is not None:
        identity = by_cluster_name.get((cluster, name))
        if identity is not None:
            return identity
        return _database_identity(cluster, name)
    matches = by_name.get(name, ())
    return matches[0] if len(matches) == 1 else None


def _backup_item(
    projection: BackupProjection,
    roots: Sequence[Path],
    database_identities: Mapping[tuple[str, str], str],
) -> tuple[ResourceInventoryItem, ResourceFinding | None]:
    backup_id = str(projection.backup.id)
    identity = f"backup:{backup_id}"
    measured = projection.occupied_bytes
    complete = (
        projection.state.value == "available" and projection.file_present and measured is not None
    )
    item = ResourceInventoryItem(
        stable_identity=identity,
        type=ResourceType.BACKUP,
        name=backup_id,
        ownership_confidence=OwnershipConfidence.PROVEN,
        active_use=(
            ActiveUse.ACTIVE if projection.state.value == "downloading" else ActiveUse.INACTIVE
        ),
        logical_bytes=projection.recorded_bytes,
        logical_bytes_semantics="recorded backup bytes",
        measured_bytes=measured,
        measured_bytes_semantics="occupied backup file bytes" if measured is not None else None,
        completeness=(
            MeasurementCompleteness.COMPLETE if complete else MeasurementCompleteness.PARTIAL
        ),
        completeness_reason=None if complete else "catalogue/file observation is incomplete",
        reclaimable=False,
        reclaimability_reason="explicit backup delete is required",
        sanitized_path=_path_display(Path(projection.backup.path), roots),
        recommendation=(
            f"backup delete {backup_id}" if projection.state.value == "available" else None
        ),
        relationships=tuple(
            database_identities.get(
                (f"{link.db_host}:{link.db_port}", link.database_name),
                _database_identity(f"{link.db_host}:{link.db_port}", link.database_name),
            )
            for link in projection.restore_links
        )
        + tuple(f"environment:{link.environment_id}" for link in projection.environment_links),
    )
    finding = None
    if projection.state.value == "available" and not projection.file_present:
        finding = ResourceFinding(
            code="available_backup_missing_file",
            severity="error",
            stable_identity=identity,
            message="available catalogue backup file is missing",
            sanitized_path=item.sanitized_path,
            recommendation=f"backup delete {backup_id}",
        )
    return item, finding


def _environment_items(
    source: EnvironmentResourceSource,
    roots: Sequence[Path],
) -> tuple[ResourceInventoryItem, ...]:
    env_id = f"environment:{source.environment_id}"
    active = (
        ActiveUse.INACTIVE
        if source.state in {"removed", "failed", "cleanup_failed"}
        else ActiveUse.ACTIVE
    )
    storage = source.storage
    worktree_bytes = storage.worktree_bytes if storage is not None else None
    python_bytes = storage.python_environment.bytes if storage is not None else None
    items = [
        ResourceInventoryItem(
            stable_identity=env_id,
            type=ResourceType.ENVIRONMENT,
            name=source.name,
            relationships=(f"project:{source.project_id}",)
            + ((f"backup:{source.backup_id}",) if source.backup_id else ()),
            ownership_confidence=OwnershipConfidence.PROVEN,
            active_use=active,
            completeness=(
                MeasurementCompleteness.COMPLETE
                if storage is not None and storage.complete
                else MeasurementCompleteness.PARTIAL
            ),
            completeness_reason=None
            if storage is not None and storage.complete
            else "storage probe unavailable",
            reclaimability_reason="environment lifecycle command is required",
            sanitized_path=_path_display(source.worktree_path, roots),
            recommendation=(
                f"env remove {source.environment_id}" if source.state == "cleanup_failed" else None
            ),
        ),
        ResourceInventoryItem(
            stable_identity=stable_resource_identity(
                "worktree", str(source.worktree_path.resolve(strict=False))
            ),
            type=ResourceType.WORKTREE,
            name=source.worktree_path.name or "worktree",
            relationships=(env_id,),
            ownership_confidence=OwnershipConfidence.PROVEN,
            active_use=active,
            measured_bytes=worktree_bytes,
            measured_bytes_semantics="filesystem bytes" if worktree_bytes is not None else None,
            completeness=(
                MeasurementCompleteness.COMPLETE
                if worktree_bytes is not None
                else MeasurementCompleteness.UNAVAILABLE
            ),
            completeness_reason=None
            if worktree_bytes is not None
            else "worktree measurement unavailable",
            reclaimability_reason="environment removal is evidence-gated",
            sanitized_path=_path_display(source.worktree_path, roots),
        ),
        ResourceInventoryItem(
            stable_identity=stable_resource_identity(
                "python_environment", str(source.python_environment_path.resolve(strict=False))
            ),
            type=ResourceType.PYTHON_ENVIRONMENT,
            name=source.python_environment_path.name or "python-environment",
            relationships=(env_id,),
            ownership_confidence=(
                OwnershipConfidence.PROVEN
                if source.python_environment_owned
                else OwnershipConfidence.SHARED
            ),
            active_use=active,
            measured_bytes=python_bytes,
            measured_bytes_semantics="filesystem bytes" if python_bytes is not None else None,
            completeness=(
                MeasurementCompleteness.COMPLETE
                if python_bytes is not None
                else MeasurementCompleteness.UNAVAILABLE
            ),
            completeness_reason=None
            if python_bytes is not None
            else "Python environment measurement unavailable",
            reclaimability_reason="shared or environment-managed Python environment",
            sanitized_path=_path_display(source.python_environment_path, roots),
        ),
    ]
    return tuple(items)


def _volume_item(source: VolumeResourceSource) -> ResourceInventoryItem:
    value = f"{source.project_id}\x00{source.volume_name}"
    identity = stable_resource_identity("volume", value)
    ownership = OwnershipConfidence.PROVEN if source.owned else OwnershipConfidence.UNKNOWN
    complete = source.available and source.usage_bytes is not None
    return ResourceInventoryItem(
        stable_identity=identity,
        type=ResourceType.VOLUME,
        name=identity,
        relationships=(f"project:{source.project_id}",),
        ownership_confidence=ownership,
        active_use=(
            ActiveUse.ACTIVE
            if source.active is True
            else ActiveUse.INACTIVE
            if source.active is False
            else ActiveUse.UNKNOWN
        ),
        measured_bytes=source.usage_bytes,
        measured_bytes_semantics="Docker volume host usage"
        if source.usage_bytes is not None
        else None,
        completeness=MeasurementCompleteness.COMPLETE
        if complete
        else MeasurementCompleteness.PARTIAL,
        completeness_reason=None
        if complete
        else _safe_message(source.reason, "volume measurement unavailable"),
        reclaimable=False,
        reclaimability_reason=(
            "retained by postgres lifecycle" if source.owned else "volume ownership is not proven"
        ),
        recommendation=None,
    )


def _file_item(source: FileResourceSource, roots: Sequence[Path]) -> ResourceInventoryItem:
    measured = _file_size(source.path)
    identity = stable_resource_identity(source.kind, str(source.path.resolve(strict=False)))
    return ResourceInventoryItem(
        stable_identity=identity,
        type=(
            ResourceType.FILESTORE
            if source.kind == "filestore"
            else ResourceType.LOG
            if source.kind == "log"
            else ResourceType.OTHER
        ),
        name=source.path.name or source.kind,
        relationships=((source.owner_identity,) if source.owner_identity else ()),
        ownership_confidence=source.ownership,
        measured_bytes=measured,
        measured_bytes_semantics="filesystem bytes" if measured is not None else None,
        completeness=(
            MeasurementCompleteness.COMPLETE
            if measured is not None
            else MeasurementCompleteness.UNAVAILABLE
        ),
        completeness_reason=None if measured is not None else "filesystem measurement unavailable",
        reclaimability_reason="preserved until ownership is proven",
        sanitized_path=_path_display(source.path, roots),
    )


def _read_only_files(roots: Iterable[Path], known: set[Path]) -> tuple[FileResourceSource, ...]:
    """Discover owned-directory files without following symlinked directories."""
    result: list[FileResourceSource] = []
    for root in roots:
        try:
            for directory, _directories, filenames in os.walk(root, followlinks=False):
                for filename in filenames:
                    path = Path(directory, filename)
                    try:
                        resolved = path.resolve(strict=False)
                    except OSError:
                        continue
                    if resolved in known or path.is_symlink():
                        continue
                    result.append(
                        FileResourceSource(
                            path=path,
                            kind="part" if path.suffix == ".part" else "owned_file",
                        )
                    )
        except OSError:
            continue
    return tuple(result)


def build_resource_inventory(  # noqa: C901
    *,
    backups: Iterable[BackupProjection] = (),
    databases: Iterable[DatabaseInventoryItem] = (),
    environments: Iterable[EnvironmentResourceSource] = (),
    projects: Iterable[ProjectResourceSource] = (),
    volumes: Iterable[VolumeResourceSource] = (),
    files: Iterable[FileResourceSource] = (),
    roots: Sequence[Path] = (),
    database_measurement_complete: bool = True,
    database_measurement_reason: str | None = None,
) -> ResourceInventory:
    """Compose deterministic resource records from already captured inputs."""
    resources: dict[str, ResourceInventoryItem] = {}
    findings: dict[tuple[str, str], ResourceFinding] = {}
    database_items = tuple(databases)
    by_cluster_name, by_name = _database_identity_maps(database_items)

    def add(item: ResourceInventoryItem) -> None:
        current = resources.get(item.stable_identity)
        if current is None:
            resources[item.stable_identity] = item
            return
        relationships = tuple(dict.fromkeys((*current.relationships, *item.relationships)))
        resources[item.stable_identity] = current.__class__(
            **{**msgspec.structs.asdict(current), "relationships": relationships}
        )

    for projection in backups:
        item, finding = _backup_item(projection, roots, by_cluster_name)
        add(item)
        if finding is not None:
            findings[(finding.code, finding.stable_identity)] = finding

    for database in database_items:
        cluster = str(database.cluster_id or database.cluster)
        identity = _database_identity(cluster, database.name)
        complete = database.logical_size_bytes is not None and database_measurement_complete
        add(
            ResourceInventoryItem(
                stable_identity=identity,
                type=ResourceType.DATABASE,
                name=database.name,
                relationships=tuple(f"environment:{value}" for value in database.environment_ids)
                + tuple(f"backup:{value}" for value in database.restore_backup_ids),
                ownership_confidence=(
                    OwnershipConfidence.PROVEN
                    if database.cluster_id is not None
                    else OwnershipConfidence.UNKNOWN
                ),
                active_use=ActiveUse.ACTIVE if database.active_sessions else ActiveUse.INACTIVE,
                logical_bytes=database.logical_size_bytes,
                logical_bytes_semantics="PostgreSQL logical database size",
                completeness=(
                    MeasurementCompleteness.COMPLETE
                    if complete
                    else MeasurementCompleteness.PARTIAL
                    if database_measurement_complete
                    else MeasurementCompleteness.UNAVAILABLE
                ),
                completeness_reason=(
                    None
                    if complete
                    else database_measurement_reason or "logical database size unavailable"
                ),
                reclaimability_reason="logical size is not host-reclaimable evidence",
                recommendation=None,
            )
        )

    for project in projects:
        identity = f"project:{project.project_id}"
        relationships: tuple[str, ...] = ()
        if project.runtime_database:
            database_edge = _database_edge(
                project.runtime_database,
                cluster=project.database_cluster,
                by_cluster_name=by_cluster_name,
                by_name=by_name,
            )
            if database_edge is not None:
                relationships = (database_edge,)
        add(
            ResourceInventoryItem(
                stable_identity=identity,
                type=ResourceType.PROJECT,
                name=project.repository_root.name or project.project_id,
                relationships=relationships,
                ownership_confidence=OwnershipConfidence.PROVEN,
                active_use=ActiveUse.UNKNOWN,
                completeness=MeasurementCompleteness.PARTIAL,
                completeness_reason="project context has no environment record",
                reclaimability_reason="project context is retained",
                sanitized_path=_path_display(project.repository_root, roots),
            )
        )

    for environment in environments:
        for item in _environment_items(environment, roots):
            add(item)
        if environment.state == "cleanup_failed":
            findings[("cleanup_failed_environment", environment.environment_id)] = ResourceFinding(
                code="cleanup_failed_environment",
                severity="error",
                stable_identity=f"environment:{environment.environment_id}",
                message=_safe_message(
                    environment.last_error, "environment cleanup failed; artifacts retained"
                ),
                recommendation=f"env remove {environment.environment_id}",
            )

    for volume in volumes:
        add(_volume_item(volume))

    for file_source in files:
        add(_file_item(file_source, roots))
        if file_source.kind == "part":
            measured = _file_size(file_source.path)
            finding = ResourceFinding(
                code="crash_left_partial_backup",
                severity="warning",
                stable_identity=stable_resource_identity(
                    "part", str(file_source.path.resolve(strict=False))
                ),
                message="unreferenced partial backup file may be a crash leftover",
                sanitized_path=_path_display(file_source.path, roots),
                measured_bytes=measured,
                recommendation="inspect before removing the partial file",
            )
            findings[(finding.code, finding.stable_identity)] = finding
        elif file_source.kind == "owned_file":
            finding = ResourceFinding(
                code="unknown_owned_directory_file",
                severity="warning",
                stable_identity=stable_resource_identity(
                    "owned_file", str(file_source.path.resolve(strict=False))
                ),
                message="unknown file was found under an SDK-owned directory",
                sanitized_path=_path_display(file_source.path, roots),
                measured_bytes=_file_size(file_source.path),
                recommendation="inspect before removing the unknown file",
            )
            findings[(finding.code, finding.stable_identity)] = finding
        if file_source.kind == "filestore" and file_source.ownership in {
            OwnershipConfidence.UNKNOWN,
            OwnershipConfidence.SHARED,
        }:
            finding = ResourceFinding(
                code="filestore_ownership_unknown",
                severity="warning",
                stable_identity=stable_resource_identity(
                    "filestore", str(file_source.path.resolve(strict=False))
                ),
                message="filestore was preserved because ownership is unknown",
                sanitized_path=_path_display(file_source.path, roots),
                recommendation=None,
            )
            findings[(finding.code, finding.stable_identity)] = finding

    complete = database_measurement_complete
    if not database_measurement_complete:
        finding = ResourceFinding(
            code="database_measurement_unavailable",
            severity="warning",
            stable_identity="database:measurement",
            message=_safe_message(
                database_measurement_reason, "database measurement is unavailable"
            ),
        )
        findings[(finding.code, finding.stable_identity)] = finding

    # Stable ordering makes Rich, JSON, and TOON projections identical.
    return ResourceInventory(
        resources=tuple(resources[key] for key in sorted(resources)),
        findings=tuple(findings[key] for key in sorted(findings)),
        complete=complete,
    )


def collect_resource_inventory(
    *,
    catalog: BackupCatalog | None = None,
    database_inventory: DatabaseInventoryResult | None = None,
    environments: Iterable[EnvironmentResourceSource] = (),
    projects: Iterable[ProjectResourceSource] = (),
    volumes: Iterable[VolumeResourceSource] = (),
    files: Iterable[FileResourceSource] = (),
    roots: Sequence[Path] = (),
    backup_root: Path | None = None,
    owned_directories: Sequence[Path] = (),
    database_measurement_reason: str | None = None,
    project_id: str | None = None,
    all_projects: bool = False,
) -> ResourceInventory:
    """Read existing catalogue projections, then build the same pure graph.

    Catalogue reads are intentionally private and are never followed by a
    repair/reconciliation call.  A missing catalogue or unavailable database
    probe is represented by incomplete output, not by synthetic lifecycle
    events.
    """
    backups: tuple[BackupProjection, ...] = ()
    supplied_files = tuple(files)
    env_sources = tuple(environments)
    project_sources = tuple(projects)
    if catalog is not None:
        page = catalog._list_backup_projections(
            include_all_states=True, limit=1000, project_id=project_id
        )
        backups = page.items
        rows = catalog._monitor_snapshot_rows(include_removed=False, project_id=project_id)
        if not env_sources:
            env_sources = tuple(
                EnvironmentResourceSource(
                    environment_id=str(row["id"]),
                    project_id=(
                        f"project_{repo_key(Path(str(row['repository_root'])), Path(str(row['git_common_dir'])))}"
                    ),
                    name=str(row["name"]),
                    worktree_path=Path(str(row["worktree_path"])),
                    python_environment_path=Path(str(row["python_environment_path"])),
                    python_environment_owned=bool(int(row["python_environment_owned"])),
                    state=str(row["state"]),
                    backup_id=str(row["backup_id"]) if row["backup_id"] else None,
                    last_error=str(row["last_error"]) if row["last_error"] else None,
                )
                for row, _runtime in rows.environments
            )
        catalog_projects = tuple(
            ProjectResourceSource(
                project_id=str(row["project_id"]),
                repository_root=Path(str(row["repository_root"])),
            )
            for row in rows.projects
        )
        if all_projects:
            by_project = {source.project_id: source for source in project_sources}
            by_project.update({source.project_id: source for source in catalog_projects})
            project_sources = tuple(by_project.values())
        elif not project_sources:
            project_sources = catalog_projects
    known_files = {
        Path(str(projection.backup.path)).resolve(strict=False)
        for projection in backups
        if projection.backup.path
    }
    discovered_roots = (
        ()
        if project_id is not None
        else tuple(owned_directories) + ((backup_root,) if backup_root else ())
    )
    discovered_files = _read_only_files(discovered_roots, known_files)
    return build_resource_inventory(
        backups=backups,
        databases=(database_inventory.databases if database_inventory is not None else ()),
        environments=env_sources,
        projects=project_sources,
        volumes=volumes,
        files=(*supplied_files, *discovered_files),
        roots=roots,
        database_measurement_complete=database_inventory is not None,
        database_measurement_reason=(
            None
            if database_inventory is not None
            else database_measurement_reason or "PostgreSQL inventory was not collected"
        ),
    )


__all__ = [
    "ActiveUse",
    "EnvironmentResourceSource",
    "FileResourceSource",
    "MeasurementCompleteness",
    "OwnershipConfidence",
    "ProjectResourceSource",
    "ResourceFinding",
    "ResourceInventory",
    "ResourceInventoryItem",
    "ResourceType",
    "VolumeResourceSource",
    "build_resource_inventory",
    "collect_resource_inventory",
    "open_catalog_read_only",
    "stable_resource_identity",
]
