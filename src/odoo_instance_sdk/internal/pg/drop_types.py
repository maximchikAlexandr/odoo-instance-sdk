"""Shared typed boundaries for guarded database-drop recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from odoo_instance_sdk.internal.proc import PreparedAction, ProcessResult

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


@dataclass(frozen=True, slots=True)
class DatabaseDropRestoreExpectation:
    cluster_id: str
    backup_id: str | None
    source_kind: str
    source_sha256: str | None
    data_directory: str | None
    restore_state: str


@dataclass(slots=True)
class DatabaseDropRestoreExpectationHolder:
    value: DatabaseDropRestoreExpectation | None = None


class DatabaseDropExecutionContext(Protocol):
    def action(self, step_id: str) -> PreparedAction: ...

    def process(self, step_id: str) -> ProcessResult: ...

    def planned(self, step_id: str) -> bool: ...

    def consumed(self, step_id: str) -> bool: ...

    def skip(self, step_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DropOwnershipEvidence:
    event_type: str
    event_sequence: int
    cluster_id: str
    compose_project: str
    volume_name: str
    backup_id: str | None
    source_kind: str
    source_sha256: str | None
    data_directory: str | None
    restore_state: str


def assert_restore_expectation(
    current: DropOwnershipEvidence,
    expected: DatabaseDropRestoreExpectation,
) -> None:
    """Reject retry execution when its immutable ownership evidence drifted."""
    if (
        current.cluster_id != expected.cluster_id
        or current.backup_id != expected.backup_id
        or current.source_kind != expected.source_kind
        or current.source_sha256 != expected.source_sha256
        or current.data_directory != expected.data_directory
        or current.restore_state != "incomplete"
        or current.restore_state != expected.restore_state
    ):
        raise ValueError("incomplete restore ownership evidence changed before mutation")


def catalog_database_in_use(
    catalog: BackupCatalog, database: str, *, allow_environment_id: str | None = None
) -> bool:
    """Return whether catalogue evidence still binds the database to live work."""
    snapshot = catalog._monitor_snapshot_rows(include_removed=False)
    for environment, runtime in snapshot.environments:
        if str(environment["id"]) != allow_environment_id and database in {
            environment["source_db_name"],
            environment["target_db_name"],
        }:
            return True
        if runtime is not None and runtime["database_name"] == database:
            return True
    return any(runtime["database_name"] == database for runtime in snapshot.project_runtimes)
