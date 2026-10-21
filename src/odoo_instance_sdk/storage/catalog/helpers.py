from __future__ import annotations

import functools
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, ParamSpec, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
)
from odoo_instance_sdk.internal.repo_key import parse_git_common_dir, repo_key
from odoo_instance_sdk.models import (
    Backup,
    BackupEvent,
    BackupEventType,
    BackupFormat,
    BackupState,
)
from odoo_instance_sdk.storage.catalog_migrate import (
    CATALOG_REVISION,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue
type CatalogValue = JsonValue | Path | datetime | uuid.UUID | tuple[str, ...]
P = ParamSpec("P")
T = TypeVar("T")
CURRENT_SCHEMA_VERSION = CATALOG_REVISION
_READ_ONLY_PROJECT_SCOPE = (
    "(project_id = ? OR (project_id IS NULL AND ? IN ("
    "SELECT MIN(p.project_id) FROM environments e "
    "JOIN projects p ON p.repository_root = e.repository_root "
    "AND p.git_common_dir = e.git_common_dir "
    "WHERE e.backup_id = backups.id "
    "GROUP BY e.backup_id "
    "HAVING COUNT(DISTINCT p.project_id) = 1)))"
)


class CopyJournalStage(StrEnum):
    PREPARED = "prepared"
    BACKED_UP = "backed_up"
    RESTORE_PENDING = "restore_pending"
    RESTORED = "restored"
    DROPPED = "dropped"
    BACKUP_DELETED = "backup_deleted"


@dataclass(frozen=True, slots=True)
class MonitorCatalogSnapshot:
    """One transactionally consistent monitor catalog read."""

    environments: tuple[tuple[sqlite3.Row, sqlite3.Row | None], ...]
    projects: tuple[sqlite3.Row, ...]
    project_runtimes: tuple[sqlite3.Row, ...]


@dataclass(frozen=True, slots=True)
class BackupRestoreLink:
    """A retained restore relationship for a catalogue backup."""

    db_host: str
    db_port: int
    database_name: str
    restored_at: datetime


@dataclass(frozen=True, slots=True)
class BackupEnvironmentLink:
    """A retained environment relationship for a catalogue backup."""

    environment_id: str
    name: str
    state: Literal["pending", "active"]
    target_database: str | None


@dataclass(frozen=True, slots=True)
class PostgresClusterClaim:
    """Persisted identity for one project-owned Compose cluster."""

    cluster_id: uuid.UUID
    project_id: str
    compose_project: str
    volume_name: str
    state: str
    created_at: datetime
    activated_at: datetime | None = None


def resolve_postgres_cluster_claim(
    get_claim: Callable[[str], PostgresClusterClaim | None],
    project_id: str,
    repository_root: str | Path,
    git_common_dir: str | Path | None,
) -> PostgresClusterClaim | None:
    """Select a claim only after proving the captured Git identity is owned.

    A removed source root is a supported recovery state: the registration and
    claim are the durable ownership record, while the root itself cannot be
    used for a live Git probe.  This exception is deliberately narrower than
    accepting a stale checkout.  It requires a captured common-dir identity,
    a lexically absent (and non-symlink) root, and the same exact/validated
    claim identities used by the live path.  Existing roots still require a
    successful read-only Git common-dir proof.
    """
    if git_common_dir is None:
        return None
    try:
        lexical_root = Path(repository_root)
        root, common = lexical_root.resolve(), Path(git_common_dir).resolve()
        root_exists = lexical_root.exists()
        missing_root = not root_exists and not lexical_root.is_symlink()
        if not missing_root:
            if parse_git_common_dir(root) != common:
                return None
        elif not common.is_dir():
            # A normal clone loses its in-root common directory with the
            # source.  Its captured lexical relationship remains durable
            # evidence; an unrelated missing path does not.
            try:
                common.relative_to(root)
            except ValueError:
                return None
    except (OSError, RuntimeError):
        return None
    claim = get_claim(project_id)
    if claim is not None:
        return claim
    legacy_project_id = repo_key(root, common)
    if project_id != f"project_{legacy_project_id}":
        return None
    legacy = get_claim(legacy_project_id)
    if legacy is None or (
        legacy.compose_project != f"odcli_pg_{legacy_project_id}"
        or legacy.volume_name != f"pgdata_{legacy_project_id}"
    ):
        return None
    return legacy


@dataclass(frozen=True, slots=True)
class BackupProjection:
    """Internal state-aware view used by point-management commands."""

    backup: Backup
    state: BackupState
    catalogue_time: datetime
    file_present: bool
    recorded_bytes: int | None
    occupied_bytes: int | None
    history: tuple[BackupEvent, ...]
    restore_links: tuple[BackupRestoreLink, ...]
    environment_links: tuple[BackupEnvironmentLink, ...]


@dataclass(frozen=True, slots=True)
class BackupProjectionPage:
    """One deterministic keyset page from the catalogue snapshot."""

    items: tuple[BackupProjection, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class BackupRetentionRecord:
    """Typed catalogue state captured for one retention decision."""

    backup_id: uuid.UUID
    backup: Backup | None
    state: BackupState | None
    timestamp: datetime | None
    source_group: tuple[str, ...] | None
    path: Path | None
    recorded_size: int
    protection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BackupRetentionSnapshot:
    """One project-scoped retention snapshot with typed revalidation facts."""

    project_id: str
    records: tuple[BackupRetentionRecord, ...]


def _translate_sqlite_error(func: Callable[P, T]) -> Callable[P, T]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as e:
            raise BackupCatalogError(str(e)) from e

    return wrapper


def _row_to_backup(row: sqlite3.Row, *, require_file: bool = True) -> Backup | None:
    if require_file and row["path"] and not Path(row["path"]).is_file():
        return None
    downloaded_at = row["downloaded_at"] or row["started_at"]
    return Backup(
        id=uuid.UUID(row["id"]),
        source_base_url=row["source_base_url"],
        database_name=row["database_name"],
        format=BackupFormat(row["format"]),
        filestore_requested=bool(row["filestore_requested"]),
        path=row["path"] or "",
        filename=row["filename"] or "",
        size_bytes=row["size_bytes"] or 0,
        sha256=row["sha256"] or "",
        downloaded_at=datetime.fromisoformat(downloaded_at),
        source_git_branch=row["source_git_branch"],
        source_name=row["source_name"],
        pinned=bool(row["pinned"]),
    )


def _row_to_cluster_claim(row: sqlite3.Row) -> PostgresClusterClaim:
    """Decode a persisted claim without silently repairing malformed evidence."""
    try:
        cluster_id = uuid.UUID(str(row["cluster_id"]))
        created_at = datetime.fromisoformat(str(row["created_at"]))
        activated_at = (
            None
            if row["activated_at"] is None
            else datetime.fromisoformat(str(row["activated_at"]))
        )
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise BackupCatalogError("postgres cluster claim contains malformed identity") from exc
    state = str(row["state"])
    if state not in {"pending", "active"}:
        raise BackupCatalogError("postgres cluster claim contains invalid state")
    return PostgresClusterClaim(
        cluster_id=cluster_id,
        project_id=str(row["project_id"]),
        compose_project=str(row["compose_project"]),
        volume_name=str(row["volume_name"]),
        state=cast("Literal['pending', 'active']", state),
        created_at=created_at,
        activated_at=activated_at,
    )


def _row_to_event(row: sqlite3.Row) -> BackupEvent:
    return BackupEvent(
        backup_id=uuid.UUID(row["backup_id"]),
        sequence=row["sequence"],
        event_type=BackupEventType(row["event_type"]),
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        path=row["path"],
        validator=row["validator"],
        exit_code=row["exit_code"],
        message=row["message"],
    )


def normalize_db_host(value: str | None) -> str:
    return "socket" if value is None else value
