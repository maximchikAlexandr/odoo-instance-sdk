"""Restore provenance operations for the backup catalog."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.models import Backup, BackupState, EnvironmentState, RestoreState
from odoo_instance_sdk.storage.catalog.helpers import (
    _row_to_backup,
    _translate_sqlite_error,
    normalize_db_host,
)
from odoo_instance_sdk.storage.catalog.provenance import restore_provenance

if TYPE_CHECKING:
    from odoo_instance_sdk.storage.catalog.helpers import PostgresClusterClaim


class _RestoreMixin:
    if TYPE_CHECKING:
        _conn: sqlite3.Connection

        def _cluster_uuid(self, value: uuid.UUID | str) -> str: ...

        def _get_postgres_cluster_by_id(self, cluster_id: str) -> PostgresClusterClaim | None: ...

    @_translate_sqlite_error
    def record_restore(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
        backup_id: str | None = None,
        *,
        source_kind: str | None = None,
        source_sha256: str | None = None,
        cluster_id: uuid.UUID | str | None = None,
        data_directory: str | Path | None = None,
        state: RestoreState | str = RestoreState.COMPLETE,
    ) -> None:
        host = normalize_db_host(db_host)
        kind, evidence_backup_id, digest = restore_provenance(backup_id, source_kind, source_sha256)
        try:
            restore_state = RestoreState(state)
        except (TypeError, ValueError) as exc:
            raise BackupCatalogError("restore state must be 'complete' or 'incomplete'") from exc
        identity = None if cluster_id is None else self._cluster_uuid(cluster_id)
        data_dir = None if data_directory is None else str(data_directory)
        if data_dir is not None and not data_dir.strip():
            raise BackupCatalogError("data_directory must not be empty")
        if identity is not None:
            claim = self._get_postgres_cluster_by_id(identity)
            if claim is None or claim.state != "active":
                raise BackupCatalogError("restore provenance requires an active cluster claim")
        with self._conn:
            self._conn.execute(
                """INSERT INTO restores
                   (db_host, db_port, database_name, backup_id, source_kind, source_sha256,
                    restored_at, cluster_id, data_directory, state)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)""",
                (
                    host,
                    db_port,
                    database_name,
                    evidence_backup_id,
                    kind,
                    digest,
                    identity,
                    data_dir,
                    restore_state.value,
                ),
            )
            self._conn.execute(
                """INSERT INTO database_events
                   (db_host, db_port, database_name, event_type, occurred_at, backup_id,
                    source_kind, source_sha256, cluster_id, data_directory)
                   VALUES (?, ?, ?, 'restored', datetime('now'), ?, ?, ?, ?, ?)""",
                (
                    host,
                    db_port,
                    database_name,
                    evidence_backup_id,
                    kind,
                    digest,
                    identity,
                    data_dir,
                ),
            )

    @_translate_sqlite_error
    def _finalize_environment_replacement(
        self,
        environment_id: str,
        backup_id: str,
        *,
        db_host: str | None,
        db_port: int,
        target_database: str,
        cluster_id: uuid.UUID | str,
        data_directory: str | Path,
    ) -> None:
        """Publish replacement provenance and environment binding atomically."""
        host = normalize_db_host(db_host)
        identity = self._cluster_uuid(cluster_id)
        data_dir = str(data_directory)
        if not data_dir.strip():
            raise BackupCatalogError("replacement data_directory must not be empty")
        with self._conn:
            environment = self._conn.execute(
                "SELECT state, db_mode, target_db_name FROM environments WHERE id=?",
                (environment_id,),
            ).fetchone()
            if (
                environment is None
                or environment["state"] not in {"ready", EnvironmentState.CLEANUP_FAILED.value}
                or environment["db_mode"] != "copy"
                or environment["target_db_name"] != target_database
            ):
                raise BackupCatalogError("environment replacement identity changed")
            backup = self._conn.execute(
                "SELECT state FROM backups WHERE id=?", (backup_id,)
            ).fetchone()
            if backup is None or backup["state"] != BackupState.AVAILABLE.value:
                raise BackupCatalogError("replacement backup is no longer available")
            claim = self._conn.execute(
                "SELECT state FROM postgres_clusters WHERE cluster_id=?", (identity,)
            ).fetchone()
            if claim is None or claim["state"] != "active":
                raise BackupCatalogError("replacement cluster claim is not active")
            self._conn.execute(
                "UPDATE environments SET backup_id=?, state='ready', last_error=NULL WHERE id=?",
                (backup_id, environment_id),
            )
            self._conn.execute(
                "INSERT INTO restores "
                "(db_host, db_port, database_name, backup_id, source_kind, source_sha256, "
                "restored_at, cluster_id, data_directory) "
                "VALUES (?, ?, ?, ?, 'catalogue', NULL, datetime('now'), ?, ?)",
                (host, db_port, target_database, backup_id, identity, data_dir),
            )
            self._conn.execute(
                "INSERT INTO database_events "
                "(db_host, db_port, database_name, event_type, occurred_at, backup_id, "
                "source_kind, source_sha256, cluster_id, data_directory) "
                "VALUES (?, ?, ?, 'restored', datetime('now'), ?, 'catalogue', NULL, ?, ?)",
                (host, db_port, target_database, backup_id, identity, data_dir),
            )
            self._conn.execute(
                "INSERT INTO environment_events "
                "(environment_id, operation, outcome, occurred_at, message) "
                "VALUES (?, 'sync', 'succeeded', datetime('now'), 'replacement')",
                (environment_id,),
            )

    @_translate_sqlite_error
    def _rollback_environment_replacement(
        self,
        environment_id: str,
        backup_id: str,
        *,
        db_host: str | None,
        db_port: int,
        target_database: str,
        cluster_id: uuid.UUID | str,
        data_directory: str | Path,
    ) -> None:
        """Restore the previous catalogue claim after post-publication compensation."""
        host = normalize_db_host(db_host)
        identity = self._cluster_uuid(cluster_id)
        data_dir = str(data_directory)
        with self._conn:
            row = self._conn.execute(
                "SELECT db_mode, target_db_name FROM environments WHERE id=?",
                (environment_id,),
            ).fetchone()
            if row is None or row["db_mode"] != "copy" or row["target_db_name"] != target_database:
                raise BackupCatalogError("environment replacement rollback identity changed")
            self._conn.execute(
                "UPDATE environments SET backup_id=?, state='ready', last_error=NULL WHERE id=?",
                (backup_id, environment_id),
            )
            self._conn.execute(
                "INSERT INTO restores "
                "(db_host, db_port, database_name, backup_id, source_kind, source_sha256, "
                "restored_at, cluster_id, data_directory) "
                "VALUES (?, ?, ?, ?, 'catalogue', NULL, datetime('now'), ?, ?)",
                (host, db_port, target_database, backup_id, identity, data_dir),
            )
            self._conn.execute(
                "INSERT INTO database_events "
                "(db_host, db_port, database_name, event_type, occurred_at, backup_id, "
                "source_kind, source_sha256, cluster_id, data_directory) "
                "VALUES (?, ?, ?, 'restored', datetime('now'), ?, 'catalogue', NULL, ?, ?)",
                (host, db_port, target_database, backup_id, identity, data_dir),
            )
            self._conn.execute(
                "INSERT INTO environment_events "
                "(environment_id, operation, outcome, occurred_at, message) "
                "VALUES (?, 'sync', 'failed', datetime('now'), 'replacement compensated')",
                (environment_id,),
            )

    @_translate_sqlite_error
    def record_database_dropped(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
    ) -> None:
        host = normalize_db_host(db_host)
        row = self._conn.execute(
            "SELECT event_type FROM database_events WHERE db_host=? AND db_port=? AND database_name=? ORDER BY sequence DESC LIMIT 1",
            (host, db_port, database_name),
        ).fetchone()
        with self._conn:
            if row is None or row["event_type"] != "dropped":
                self._conn.execute(
                    "INSERT INTO database_events (db_host, db_port, database_name, event_type, occurred_at, backup_id) VALUES (?, ?, ?, 'dropped', datetime('now'), NULL)",
                    (host, db_port, database_name),
                )
            self._conn.execute(
                "DELETE FROM restores WHERE db_host=? AND db_port=? AND database_name=? "
                "AND state='incomplete'",
                (host, db_port, database_name),
            )

    @_translate_sqlite_error
    def record_databases_dropped(
        self,
        db_host: str | None,
        db_port: int,
        database_names: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Record proven drops in one catalogue transaction.

        The latest-event check retains the existing idempotency rule while the
        surrounding transaction prevents a partial multi-name reconciliation.
        """
        host = normalize_db_host(db_host)
        changed: list[str] = []
        with self._conn:
            for database_name in database_names:
                row = self._conn.execute(
                    "SELECT event_type FROM database_events "
                    "WHERE db_host=? AND db_port=? AND database_name=? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (host, db_port, database_name),
                ).fetchone()
                if row is None or row["event_type"] != "dropped":
                    self._conn.execute(
                        "INSERT INTO database_events "
                        "(db_host, db_port, database_name, event_type, occurred_at, backup_id) "
                        "VALUES (?, ?, ?, 'dropped', datetime('now'), NULL)",
                        (host, db_port, database_name),
                    )
                    changed.append(database_name)
                self._conn.execute(
                    "DELETE FROM restores WHERE db_host=? AND db_port=? AND database_name=? "
                    "AND state='incomplete'",
                    (host, db_port, database_name),
                )
        return tuple(changed)

    @_translate_sqlite_error
    def _record_database_bootstrapped(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
        *,
        cluster_id: uuid.UUID | str,
        data_directory: str | Path,
    ) -> None:
        """Publish ownership evidence for one newly-created Compose ``tmp``."""
        host = normalize_db_host(db_host)
        if database_name != "tmp":
            raise BackupCatalogError("bootstrap provenance requires database tmp")
        if not isinstance(data_directory, (str, Path)) or not str(data_directory).strip():
            raise BackupCatalogError("bootstrap provenance requires a data directory")
        identity = self._cluster_uuid(cluster_id)
        claim = self._get_postgres_cluster_by_id(identity)
        if claim is None or claim.state != "active":
            raise BackupCatalogError("bootstrap provenance requires an active cluster claim")
        with self._conn:
            self._conn.execute(
                """INSERT INTO database_events
                   (db_host, db_port, database_name, event_type, occurred_at, backup_id,
                    source_kind, source_sha256, cluster_id, data_directory)
                   VALUES (?, ?, ?, 'bootstrapped', datetime('now'), NULL,
                           NULL, NULL, ?, ?)""",
                (host, db_port, database_name, identity, str(data_directory).strip()),
            )

    @_translate_sqlite_error
    def _latest_database_event(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
    ) -> sqlite3.Row | None:
        """Read the latest exact lifecycle event by append sequence."""
        host = normalize_db_host(db_host)
        return cast(
            "sqlite3.Row | None",
            self._conn.execute(
                "SELECT * FROM database_events "
                "WHERE db_host=? AND db_port=? AND database_name=? "
                "ORDER BY sequence DESC LIMIT 1",
                (host, db_port, database_name),
            ).fetchone(),
        )

    @_translate_sqlite_error
    def latest_restore(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
    ) -> Backup | None:
        host = normalize_db_host(db_host)
        row = self._conn.execute(
            "SELECT b.*, r.backup_id AS restore_backup_id, r.restored_at, "
            "r.state AS restore_state "
            "FROM restores r LEFT JOIN backups b ON b.id = r.backup_id "
            "WHERE r.db_host=? AND r.db_port=? AND r.database_name=? "
            "ORDER BY r.restored_at DESC, r.sequence DESC LIMIT 1",
            (host, db_port, database_name),
        ).fetchone()
        if row is None or row["restore_backup_id"] is None or row["restore_state"] != "complete":
            return None
        if row["state"] == BackupState.DELETED.value:
            return None
        if not row["path"]:
            return None
        return _row_to_backup(row)

    @_translate_sqlite_error
    def latest_restore_provenance(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
    ) -> Backup | None:
        """Return the mapped backup audit row without availability checks.

        This is intentionally separate from :meth:`latest_restore`: callers
        deciding freshness or restore input still need the latter's available
        file semantics, while provenance remains valid after deletion or a
        missing archive.
        """
        host = normalize_db_host(db_host)
        row = self._conn.execute(
            "SELECT b.*, r.backup_id AS restore_backup_id, r.restored_at, "
            "r.state AS restore_state "
            "FROM restores r LEFT JOIN backups b ON b.id = r.backup_id "
            "WHERE r.db_host=? AND r.db_port=? AND r.database_name=? "
            "ORDER BY r.restored_at DESC, r.sequence DESC LIMIT 1",
            (host, db_port, database_name),
        ).fetchone()
        if row is None or row["restore_backup_id"] is None or row["restore_state"] != "complete":
            return None
        return _row_to_backup(row, require_file=False)

    @_translate_sqlite_error
    def _list_restore_bindings(self, db_host: str | None, db_port: int) -> list[sqlite3.Row]:
        """Read exact restore identities for internal database projections."""
        host = normalize_db_host(db_host)
        return cast(
            "list[sqlite3.Row]",
            self._conn.execute(
                "SELECT database_name, backup_id, source_kind, source_sha256, cluster_id, "
                "data_directory, restored_at, state "
                "FROM restores WHERE db_host=? AND db_port=? "
                "ORDER BY database_name ASC, restored_at DESC, sequence DESC",
                (host, db_port),
            ).fetchall(),
        )

    @_translate_sqlite_error
    def _latest_restore_binding(
        self, db_host: str | None, db_port: int, database_name: str
    ) -> sqlite3.Row | None:
        """Read the latest exact restore identity for internal ownership gates."""
        host = normalize_db_host(db_host)
        return cast(
            "sqlite3.Row | None",
            self._conn.execute(
                "SELECT database_name, backup_id, source_kind, source_sha256, cluster_id, "
                "data_directory, restored_at, state "
                "FROM restores WHERE db_host=? AND db_port=? AND database_name=? "
                "ORDER BY restored_at DESC, sequence DESC LIMIT 1",
                (host, db_port, database_name),
            ).fetchone(),
        )

    @_translate_sqlite_error
    def distinct_restored_database_names(
        self,
        db_host: str | None,
        db_port: int,
    ) -> tuple[str, ...]:
        host = normalize_db_host(db_host)
        rows = self._conn.execute(
            "SELECT DISTINCT database_name FROM restores WHERE db_host=? AND db_port=?",
            (host, db_port),
        ).fetchall()
        return tuple(row["database_name"] for row in rows)

    @_translate_sqlite_error
    def has_tracked_database(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
    ) -> bool:
        host = normalize_db_host(db_host)
        row = self._conn.execute(
            "SELECT 1 FROM restores WHERE db_host=? AND db_port=? AND database_name=? LIMIT 1",
            (host, db_port, database_name),
        ).fetchone()
        return row is not None
