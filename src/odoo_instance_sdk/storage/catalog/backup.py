from __future__ import annotations  # noqa: I001

import base64
import binascii
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    BackupNotAvailableError,
    BackupNotFoundError,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.models import (
    Backup,
    BackupEvent,
    BackupEventType,
    BackupState,
    BackupValidationStatus,
    EnvironmentState,
)
from odoo_instance_sdk.storage.catalog.helpers import (
    _READ_ONLY_PROJECT_SCOPE as _READ_ONLY_PROJECT_SCOPE,
    BackupEnvironmentLink as BackupEnvironmentLink,
    BackupProjection as BackupProjection,
    BackupProjectionPage as BackupProjectionPage,
    BackupRestoreLink as BackupRestoreLink,
    CatalogValue as CatalogValue,
    CopyJournalStage as CopyJournalStage,
    MonitorCatalogSnapshot as MonitorCatalogSnapshot,
    PostgresClusterClaim as PostgresClusterClaim,
    _row_to_backup as _row_to_backup,
    _row_to_cluster_claim as _row_to_cluster_claim,
    _row_to_event as _row_to_event,
    _translate_sqlite_error as _translate_sqlite_error,
    normalize_db_host as normalize_db_host,
)
from odoo_instance_sdk.storage.catalog_migrate import (
    ensure_catalog_migrated,
)


class _BackupMixin:
    if TYPE_CHECKING:
        _conn: sqlite3.Connection
        db_path: Path
        _read_only: bool

        def _add_event(
            self,
            backup_id: str,
            event_type: str,
            path: str | None = None,
            validator: str | None = None,
            exit_code: int | None = None,
            message: str | None = None,
        ) -> None: ...

    def __post_init__(self) -> None:
        try:
            from odoo_instance_sdk.internal.paths import _user_root

            if (
                self.db_path.resolve(strict=False)
                == _user_root(ensure_exists=False) / "catalog.sqlite3"
            ):
                from odoo_instance_sdk.internal.storage_migration import ensure_storage_migrated

                ensure_storage_migrated()
            self._conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            ensure_catalog_migrated(self.db_path)
            self.db_path.chmod(0o600)
            for sidecar in (
                self.db_path.with_suffix(self.db_path.suffix + "-wal"),
                self.db_path.with_suffix(self.db_path.suffix + "-shm"),
            ):
                if sidecar.exists():
                    sidecar.chmod(0o600)
        except sqlite3.Error as e:
            raise BackupCatalogError(str(e)) from e
        except OSError as e:
            raise BackupCatalogError(f"Failed to set permissions on catalog file: {e}") from e

    @_translate_sqlite_error
    def _register_project(
        self, project_id: str, repository_root: str | Path, git_common_dir: str | Path
    ) -> None:
        """Register a canonical initialized project at an authorized write point."""
        root = Path(repository_root).resolve()
        common = Path(git_common_dir).resolve()
        expected = f"project_{repo_key(root, common)}"
        if project_id != expected:
            raise BackupCatalogError(
                "project registration identity does not match repository metadata"
            )
        with self._conn:
            self._conn.execute(
                """INSERT INTO projects
                   (project_id, repository_root, git_common_dir, registered_at, updated_at)
                   VALUES (?, ?, ?, datetime('now'), datetime('now'))
                   ON CONFLICT(project_id) DO UPDATE SET
                     repository_root=excluded.repository_root,
                     git_common_dir=excluded.git_common_dir,
                     updated_at=excluded.updated_at""",
                (project_id, str(root), str(common)),
            )

    def close(self) -> None:
        self._conn.close()

    @_translate_sqlite_error
    def start_download(
        self,
        backup_id: str,
        source_base_url: str,
        database_name: str,
        format: str,
        filestore_requested: bool,
        path: Path,
        *,
        source_git_branch: str | None = None,
        project_id: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO backups (id, source_base_url, database_name, format, filestore_requested, path, state, started_at, source_git_branch, project_id) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)",
            (
                backup_id,
                source_base_url,
                database_name,
                format,
                int(filestore_requested),
                str(path),
                BackupState.DOWNLOADING.value,
                source_git_branch,
                project_id,
            ),
        )
        self._add_event(backup_id, "download_started", path=str(path))
        self._conn.commit()

    @_translate_sqlite_error
    def success_download(
        self,
        backup_id: str,
        filename: str,
        size_bytes: int,
        sha256: str,
        *,
        downloaded_at: datetime | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE backups SET state=?, filename=?, size_bytes=?, sha256=?, "
            "downloaded_at=COALESCE(?, datetime('now')) WHERE id=?",
            (
                BackupState.AVAILABLE.value,
                filename,
                size_bytes,
                sha256,
                downloaded_at.isoformat() if downloaded_at is not None else None,
                backup_id,
            ),
        )
        self._add_event(backup_id, "download_succeeded")
        self._conn.commit()

    @_translate_sqlite_error
    def fail_download(self, backup_id: str, error_type: str, error_message: str) -> None:
        sanitized = error_message[:4096]
        self._conn.execute(
            "UPDATE backups SET state=?, failed_at=datetime('now'), error_type=?, error_message=? WHERE id=?",
            (BackupState.FAILED.value, error_type, sanitized, backup_id),
        )
        self._add_event(backup_id, "download_failed", message=sanitized)
        self._conn.commit()

    @_translate_sqlite_error
    def record_validation(
        self,
        backup_id: str,
        status: BackupValidationStatus,
        validator: str | None = None,
        exit_code: int | None = None,
        message: str | None = None,
    ) -> None:
        if status is BackupValidationStatus.VALID:
            event_type = BackupEventType.VALIDATION_SUCCEEDED.value
        elif status is BackupValidationStatus.INVALID:
            event_type = BackupEventType.VALIDATION_FAILED.value
        else:
            event_type = BackupEventType.VALIDATION_UNAVAILABLE.value
        self._add_event(
            backup_id,
            event_type,
            validator=validator,
            exit_code=exit_code,
            message=message,
        )
        self._conn.commit()

    @_translate_sqlite_error
    def record_deletion(self, backup_id: str) -> None:
        self._conn.execute(
            "UPDATE backups SET state=?, deleted_at=datetime('now') WHERE id=?",
            (BackupState.DELETED.value, backup_id),
        )
        self._add_event(backup_id, "deleted")
        self._conn.commit()

    @_translate_sqlite_error
    def get_by_id(self, backup_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM backups WHERE id = ?",
            (backup_id,),
        ).fetchone()
        return row

    @staticmethod
    def _canonical_backup_id(backup_id: str) -> str:
        if not isinstance(backup_id, str):
            raise BackupNotFoundError("Backup identifier must be a complete UUID")
        try:
            parsed = uuid.UUID(backup_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise BackupNotFoundError("Backup identifier must be a complete UUID") from exc
        if str(parsed) != backup_id.lower():
            raise BackupNotFoundError("Backup identifier must be a complete UUID")
        return str(parsed)

    @staticmethod
    def _encode_backup_cursor(catalogue_time: str, backup_id: str) -> str:
        payload = json.dumps(
            {"catalogue_time": catalogue_time, "id": backup_id},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def _decode_backup_cursor(cls, cursor: str) -> tuple[str, str]:
        if not isinstance(cursor, str) or not cursor:
            raise BackupCatalogError("Backup cursor is invalid")
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
            )
        except (ValueError, TypeError, binascii.Error) as exc:
            raise BackupCatalogError("Backup cursor is invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {"catalogue_time", "id"}:
            raise BackupCatalogError("Backup cursor is invalid")
        catalogue_time = payload["catalogue_time"]
        if not isinstance(catalogue_time, str) or not catalogue_time:
            raise BackupCatalogError("Backup cursor is invalid")
        try:
            backup_id = cls._canonical_backup_id(payload["id"])
        except BackupNotFoundError as exc:
            raise BackupCatalogError("Backup cursor is invalid") from exc
        return catalogue_time, backup_id

    @staticmethod
    def _projection_file_data(path_value: str | None) -> tuple[bool, int | None]:
        if not path_value:
            return False, None
        path = Path(path_value)
        file_present = path.exists()
        if not file_present or path.is_symlink() or not path.is_file():
            return file_present, None
        try:
            return True, path.stat().st_size
        except OSError:
            return True, None

    def _projection_from_row(self, row: sqlite3.Row) -> BackupProjection:
        backup = _row_to_backup(row, require_file=False)
        if backup is None:  # pragma: no cover - rows are complete catalogue records
            raise BackupCatalogError("catalogue backup row is incomplete")
        history_rows = self._conn.execute(
            "SELECT * FROM backup_events WHERE backup_id=? ORDER BY sequence ASC",
            (row["id"],),
        ).fetchall()
        restore_rows = self._conn.execute(
            "SELECT db_host, db_port, database_name, restored_at FROM restores "
            "WHERE backup_id=? ORDER BY restored_at ASC, rowid ASC",
            (row["id"],),
        ).fetchall()
        environment_rows = self._conn.execute(
            "SELECT id, name, state, target_db_name FROM environments "
            "WHERE backup_id=? ORDER BY created_at ASC, id ASC",
            (row["id"],),
        ).fetchall()
        file_present, occupied_bytes = self._projection_file_data(row["path"])
        catalogue_raw = row["downloaded_at"] or row["started_at"]
        return BackupProjection(
            backup=backup,
            state=BackupState(row["state"]),
            catalogue_time=datetime.fromisoformat(catalogue_raw),
            file_present=file_present,
            recorded_bytes=row["size_bytes"],
            occupied_bytes=occupied_bytes,
            history=tuple(_row_to_event(item) for item in history_rows),
            restore_links=tuple(
                BackupRestoreLink(
                    db_host=row_item["db_host"],
                    db_port=row_item["db_port"],
                    database_name=row_item["database_name"],
                    restored_at=datetime.fromisoformat(row_item["restored_at"]),
                )
                for row_item in restore_rows
            ),
            environment_links=tuple(
                BackupEnvironmentLink(
                    environment_id=row_item["id"],
                    name=row_item["name"],
                    state=row_item["state"],
                    target_database=row_item["target_db_name"],
                )
                for row_item in environment_rows
            ),
        )

    @_translate_sqlite_error
    def _resolve_backup_projection(self, backup_id: str) -> BackupProjection:
        """Resolve one complete UUID without consulting the filesystem index."""
        canonical_id = self._canonical_backup_id(backup_id)
        self._conn.execute("BEGIN")
        try:
            row = self._conn.execute("SELECT * FROM backups WHERE id=?", (canonical_id,)).fetchone()
            if row is None:
                raise BackupNotFoundError(f"Backup {canonical_id} not found in catalog")
            return self._projection_from_row(row)
        finally:
            self._conn.rollback()

    @_translate_sqlite_error
    def _list_backup_projections(
        self,
        *,
        source_base_url: str | None = None,
        database_name: str | None = None,
        format: str | None = None,
        include_all_states: bool = False,
        project_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> BackupProjectionPage:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise BackupCatalogError("Backup limit must be an integer between 1 and 1000")
        after: tuple[str, str] | None = None
        self._backfill_deterministic_backup_ownership()
        if cursor is not None:
            after = self._decode_backup_cursor(cursor)
        clauses: list[str] = []
        params: list[str | int] = []
        if not include_all_states:
            clauses.append("state = ?")
            params.append(BackupState.AVAILABLE.value)
        if source_base_url is not None:
            clauses.append("source_base_url = ?")
            params.append(source_base_url)
        if database_name is not None:
            clauses.append("database_name = ?")
            params.append(database_name)
        if format is not None:
            clauses.append("format = ?")
            params.append(format)
        if project_id is not None:
            clauses.append(_READ_ONLY_PROJECT_SCOPE if self._read_only else "project_id = ?")
            params.extend((project_id, project_id) if self._read_only else (project_id,))
        if after is not None:
            clauses.append(
                "(COALESCE(downloaded_at, started_at) < ? OR "
                "(COALESCE(downloaded_at, started_at) = ? AND id > ?))"
            )
            params.extend((after[0], after[0], after[1]))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT *, COALESCE(downloaded_at, started_at) AS catalogue_time "
            f"FROM backups{where} ORDER BY catalogue_time DESC, id ASC LIMIT ?"
        )
        self._conn.execute("BEGIN")
        try:
            rows = self._conn.execute(query, (*params, limit + 1)).fetchall()
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = tuple(self._projection_from_row(row) for row in page_rows)
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = self._encode_backup_cursor(last["catalogue_time"], last["id"])
            return BackupProjectionPage(items=items, next_cursor=next_cursor)
        finally:
            self._conn.rollback()

    @_translate_sqlite_error
    def update_path(self, backup_id: str, path: Path) -> None:
        self._conn.execute(
            "UPDATE backups SET path = ? WHERE id = ?",
            (str(path), backup_id),
        )
        self._conn.commit()

    @_translate_sqlite_error
    def list_backups(
        self,
        source_base_url: str | None = None,
        database_name: str | None = None,
        format: str | None = None,
        project_id: str | None = None,
    ) -> list[Backup]:
        self._backfill_deterministic_backup_ownership()
        query = "SELECT * FROM backups WHERE state = ?"
        params: list[str | int | None] = [BackupState.AVAILABLE.value]
        if source_base_url is not None:
            query += " AND source_base_url = ?"
            params.append(source_base_url)
        if database_name is not None:
            query += " AND database_name = ?"
            params.append(database_name)
        if format is not None:
            query += " AND format = ?"
            params.append(format)
        if project_id is not None:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY downloaded_at DESC, id DESC"
        rows = self._conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            b = _row_to_backup(r)
            if b is not None:
                result.append(b)
        return result

    @_translate_sqlite_error
    def latest_backup(
        self,
        source_base_url: str,
        database_name: str,
        format: str | None = None,
        project_id: str | None = None,
    ) -> Backup | None:
        self._backfill_deterministic_backup_ownership()
        query = (
            "SELECT * FROM backups WHERE state = ? AND source_base_url = ? AND database_name = ?"
        )
        params: list[str | int | None] = [
            BackupState.AVAILABLE.value,
            source_base_url,
            database_name,
        ]
        if format is not None:
            query += " AND format = ?"
            params.append(format)
        if project_id is not None:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY downloaded_at DESC, id DESC LIMIT 1"
        row = self._conn.execute(query, params).fetchone()
        if row is None:
            return None
        return _row_to_backup(row)

    @_translate_sqlite_error
    def _backfill_deterministic_backup_ownership(self) -> None:
        """Repair only legacy rows whose environment evidence has one owner."""
        if self._read_only:
            return
        environment_columns = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(environments)")
        }
        if "backup_id" not in environment_columns:
            return
        self._conn.execute(
            """UPDATE backups
               SET project_id = (
                   SELECT MIN(p.project_id)
                   FROM environments e
                   JOIN projects p ON p.repository_root = e.repository_root
                                  AND p.git_common_dir = e.git_common_dir
                   WHERE e.backup_id = backups.id
               )
               WHERE project_id IS NULL
                 AND (SELECT COUNT(DISTINCT p.project_id)
                      FROM environments e
                      JOIN projects p ON p.repository_root = e.repository_root
                                     AND p.git_common_dir = e.git_common_dir
                      WHERE e.backup_id = backups.id) = 1"""
        )
        self._conn.commit()

    @_translate_sqlite_error
    def relink_backup_project(self, backup_id: str, project_id: str) -> None:
        """Relink an already-unowned backup row to a resolved canonical project.

        The UUID, file, and history remain unchanged; only ``project_id`` is
        set.  The target project MUST already be registered.  Automatic
        ambiguous backfill is not performed; callers resolve the owner
        explicitly before invoking this repair path.
        """
        canonical_id = self._canonical_backup_id(backup_id)
        project = self._cluster_text(project_id, "project_id")
        row = self._conn.execute(
            "SELECT project_id FROM backups WHERE id = ?", (canonical_id,)
        ).fetchone()
        if row is None:
            raise BackupNotFoundError(f"Backup {canonical_id} not found in catalog")
        if row["project_id"] is not None:
            raise BackupCatalogError(
                f"Backup {canonical_id} is already owned by {row['project_id']}"
            )
        registered = self._conn.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project,)
        ).fetchone()
        if registered is None:
            raise BackupCatalogError(f"project {project} is not registered")
        with self._conn:
            self._conn.execute(
                "UPDATE backups SET project_id = ? WHERE id = ?",
                (project, canonical_id),
            )

    @_translate_sqlite_error
    def get_backup_history(
        self,
        source_base_url: str | None = None,
        database_name: str | None = None,
        backup_id: str | None = None,
    ) -> list[BackupEvent]:
        query = """SELECT e.* FROM backup_events e
                   JOIN backups b ON b.id = e.backup_id
                   WHERE 1=1"""
        params: list[str | int | None] = []
        if backup_id is not None:
            query += " AND e.backup_id = ?"
            params.append(backup_id)
        if source_base_url is not None:
            query += " AND b.source_base_url = ?"
            params.append(source_base_url)
        if database_name is not None:
            query += " AND b.database_name = ?"
            params.append(database_name)
        query += " ORDER BY e.sequence DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_event(r) for r in rows]

    @_translate_sqlite_error
    def verify_identity(self, backup: Backup, *, verify_content: bool = False) -> None:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM backups WHERE id = ?", (str(backup.id),)
        ).fetchone()
        if row is None:
            raise BackupNotFoundError(f"Backup {backup.id} not found in catalog")
        if row["state"] != BackupState.AVAILABLE.value:
            raise BackupNotAvailableError(
                f"Backup {backup.id} is in state {row['state']!r}, not available"
            )
        expected = (
            ("source_base_url", row["source_base_url"], backup.source_base_url),
            ("filename", row["filename"], backup.filename),
            ("path", row["path"], backup.path),
            ("format", row["format"], backup.format.value),
            ("filestore_requested", bool(row["filestore_requested"]), backup.filestore_requested),
            ("database_name", row["database_name"], backup.database_name),
            ("size_bytes", row["size_bytes"], backup.size_bytes),
            ("sha256", row["sha256"], backup.sha256),
            ("source_git_branch", row["source_git_branch"], backup.source_git_branch),
        )
        mismatches = [name for name, actual, expected_val in expected if actual != expected_val]
        if mismatches:
            raise BackupNotAvailableError(
                f"Backup {backup.id} metadata mismatch: {', '.join(mismatches)}"
            )
        if verify_content and backup.sha256:
            path = Path(backup.path)
            if not path.is_file():
                raise BackupNotAvailableError(f"Backup file not found: {backup.path}")
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != backup.sha256:
                raise BackupNotAvailableError(
                    f"Backup {backup.id} content hash mismatch (tampered or modified)"
                )

    @staticmethod
    def _cluster_uuid(value: uuid.UUID | str) -> str:
        try:
            parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise BackupCatalogError("cluster_id must be a complete UUID") from exc
        return str(parsed)

    @staticmethod
    def _cluster_text(value: str, label: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(ord(char) < 0x20 for char in value)
        ):
            raise BackupCatalogError(f"{label} must be non-empty text")
        return value.strip()

    @_translate_sqlite_error
    def _get_postgres_cluster(self, project_id: str) -> PostgresClusterClaim | None:
        """Return the one persisted claim for a project, if it exists."""
        project = self._cluster_text(project_id, "project_id")
        row = self._conn.execute(
            "SELECT * FROM postgres_clusters WHERE project_id = ?", (project,)
        ).fetchone()
        return _row_to_cluster_claim(row) if row is not None else None

    @_translate_sqlite_error
    def _get_postgres_cluster_by_id(
        self, cluster_id: uuid.UUID | str
    ) -> PostgresClusterClaim | None:
        identifier = self._cluster_uuid(cluster_id)
        row = self._conn.execute(
            "SELECT * FROM postgres_clusters WHERE cluster_id = ?", (identifier,)
        ).fetchone()
        return _row_to_cluster_claim(row) if row is not None else None

    @_translate_sqlite_error
    def _ensure_postgres_cluster_pending(
        self,
        project_id: str,
        compose_project: str,
        volume_name: str,
    ) -> PostgresClusterClaim:
        """Create or reuse a pending claim without replacing its identity."""
        project = self._cluster_text(project_id, "project_id")
        compose = self._cluster_text(compose_project, "compose_project")
        volume = self._cluster_text(volume_name, "volume_name")
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM postgres_clusters WHERE project_id = ?", (project,)
            ).fetchone()
            if row is not None:
                claim = _row_to_cluster_claim(row)
                if claim.compose_project != compose or claim.volume_name != volume:
                    raise BackupCatalogError("existing postgres cluster claim does not match")
                return claim
            identifier = str(uuid.uuid4())
            self._conn.execute(
                """INSERT INTO postgres_clusters
                   (cluster_id, project_id, compose_project, volume_name, state, created_at)
                   VALUES (?, ?, ?, ?, 'pending', datetime('now'))""",
                (identifier, project, compose, volume),
            )
            created = self._conn.execute(
                "SELECT * FROM postgres_clusters WHERE cluster_id = ?", (identifier,)
            ).fetchone()
            assert created is not None
            return _row_to_cluster_claim(created)

    @_translate_sqlite_error
    def _activate_postgres_cluster(
        self,
        cluster_id: uuid.UUID | str,
        project_id: str,
        compose_project: str,
        volume_name: str,
    ) -> PostgresClusterClaim:
        """Promote only the exact pending claim after external inspection."""
        identifier = self._cluster_uuid(cluster_id)
        project = self._cluster_text(project_id, "project_id")
        compose = self._cluster_text(compose_project, "compose_project")
        volume = self._cluster_text(volume_name, "volume_name")
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM postgres_clusters WHERE cluster_id = ?", (identifier,)
            ).fetchone()
            if row is None:
                raise BackupCatalogError("postgres cluster claim does not exist")
            claim = _row_to_cluster_claim(row)
            if (
                claim.project_id != project
                or claim.compose_project != compose
                or claim.volume_name != volume
            ):
                raise BackupCatalogError("postgres cluster claim identity does not match")
            if claim.state == "active":
                return claim
            if claim.state != "pending":
                raise BackupCatalogError("postgres cluster claim has an invalid state")
            self._conn.execute(
                "UPDATE postgres_clusters SET state='active', activated_at=datetime('now') "
                "WHERE cluster_id = ? AND state='pending'",
                (identifier,),
            )
            activated = self._conn.execute(
                "SELECT * FROM postgres_clusters WHERE cluster_id = ?", (identifier,)
            ).fetchone()
            assert activated is not None
            return _row_to_cluster_claim(activated)

    @_translate_sqlite_error
    def record_restore(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
        backup_id: str,
        *,
        cluster_id: uuid.UUID | str | None = None,
        data_directory: str | Path | None = None,
    ) -> None:
        host = normalize_db_host(db_host)
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
                   (db_host, db_port, database_name, backup_id, restored_at, cluster_id, data_directory)
                   VALUES (?, ?, ?, ?, datetime('now'), ?, ?)""",
                (host, db_port, database_name, backup_id, identity, data_dir),
            )
            self._conn.execute(
                """INSERT INTO database_events
                   (db_host, db_port, database_name, event_type, occurred_at, backup_id, cluster_id, data_directory)
                   VALUES (?, ?, ?, 'restored', datetime('now'), ?, ?, ?)""",
                (host, db_port, database_name, backup_id, identity, data_dir),
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
                "(db_host, db_port, database_name, backup_id, restored_at, cluster_id, data_directory) "
                "VALUES (?, ?, ?, ?, datetime('now'), ?, ?)",
                (host, db_port, target_database, backup_id, identity, data_dir),
            )
            self._conn.execute(
                "INSERT INTO database_events "
                "(db_host, db_port, database_name, event_type, occurred_at, backup_id, cluster_id, data_directory) "
                "VALUES (?, ?, ?, 'restored', datetime('now'), ?, ?, ?)",
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
                "(db_host, db_port, database_name, backup_id, restored_at, cluster_id, data_directory) "
                "VALUES (?, ?, ?, ?, datetime('now'), ?, ?)",
                (host, db_port, target_database, backup_id, identity, data_dir),
            )
            self._conn.execute(
                "INSERT INTO database_events "
                "(db_host, db_port, database_name, event_type, occurred_at, backup_id, cluster_id, data_directory) "
                "VALUES (?, ?, ?, 'restored', datetime('now'), ?, ?, ?)",
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
        if row is not None and row["event_type"] == "dropped":
            return
        self._conn.execute(
            "INSERT INTO database_events (db_host, db_port, database_name, event_type, occurred_at, backup_id) VALUES (?, ?, ?, 'dropped', datetime('now'), NULL)",
            (host, db_port, database_name),
        )
        self._conn.commit()

    @_translate_sqlite_error
    def latest_restore(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
    ) -> Backup | None:
        host = normalize_db_host(db_host)
        row = self._conn.execute(
            "SELECT b.*, r.restored_at FROM restores r INNER JOIN backups b ON b.id = r.backup_id WHERE r.db_host=? AND r.db_port=? AND r.database_name=? ORDER BY r.restored_at DESC LIMIT 1",
            (host, db_port, database_name),
        ).fetchone()
        if row is None:
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
            "SELECT b.*, r.restored_at FROM restores r "
            "INNER JOIN backups b ON b.id = r.backup_id "
            "WHERE r.db_host=? AND r.db_port=? AND r.database_name=? "
            "ORDER BY r.restored_at DESC, r.sequence DESC LIMIT 1",
            (host, db_port, database_name),
        ).fetchone()
        if row is None:
            return None
        return _row_to_backup(row, require_file=False)

    @_translate_sqlite_error
    def _list_restore_bindings(self, db_host: str | None, db_port: int) -> list[sqlite3.Row]:
        """Read exact restore identities for internal database projections."""
        host = normalize_db_host(db_host)
        return self._conn.execute(
            "SELECT database_name, backup_id, cluster_id, data_directory, restored_at "
            "FROM restores WHERE db_host=? AND db_port=? "
            "ORDER BY database_name ASC, restored_at DESC, sequence DESC",
            (host, db_port),
        ).fetchall()

    @_translate_sqlite_error
    def _latest_restore_binding(
        self, db_host: str | None, db_port: int, database_name: str
    ) -> sqlite3.Row | None:
        """Read the latest exact restore identity for internal ownership gates."""
        host = normalize_db_host(db_host)
        return cast(
            "sqlite3.Row | None",
            self._conn.execute(
                "SELECT database_name, backup_id, cluster_id, data_directory, restored_at "
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
