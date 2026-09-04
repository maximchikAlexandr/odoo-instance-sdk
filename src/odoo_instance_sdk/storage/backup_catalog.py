from __future__ import annotations

import functools
import hashlib
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
    BackupNotAvailableError,
    BackupNotFoundError,
)
from odoo_instance_sdk.internal.repo_key import repo_key
from odoo_instance_sdk.internal.sanitize import sanitize_event_message, sanitize_last_error
from odoo_instance_sdk.models import (
    Backup,
    BackupEvent,
    BackupEventType,
    BackupFormat,
    BackupState,
    BackupValidationStatus,
)

if TYPE_CHECKING:
    from odoo_instance_sdk.execution import JsonValue

type CatalogValue = JsonValue | Path | datetime | uuid.UUID | tuple[str, ...]

P = ParamSpec("P")
T = TypeVar("T")
CURRENT_SCHEMA_VERSION = 11


class CopyJournalStage(StrEnum):
    PREPARED = "prepared"
    BACKED_UP = "backed_up"
    RESTORE_PENDING = "restore_pending"
    RESTORED = "restored"
    DROPPED = "dropped"
    BACKUP_DELETED = "backup_deleted"


def _translate_sqlite_error(func: Callable[P, T]) -> Callable[P, T]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as e:
            raise BackupCatalogError(str(e)) from e

    return wrapper


@dataclass(slots=True, kw_only=True)
class BackupCatalog:
    db_path: Path
    _conn: sqlite3.Connection = field(init=False, repr=False)
    # Filled by the atomic monitor read and consumed by the monitor collector.
    # Keeping this private preserves the long-standing environment-only API.
    _last_monitor_projects: list[sqlite3.Row] = field(init=False, repr=False, default_factory=list)
    _last_monitor_project_runtimes: list[sqlite3.Row] = field(
        init=False, repr=False, default_factory=list
    )

    def __post_init__(self) -> None:
        try:
            self._conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._create_schema(self._conn)
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

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS backups (
                id TEXT PRIMARY KEY,
                source_base_url TEXT NOT NULL,
                database_name TEXT NOT NULL,
                format TEXT NOT NULL CHECK (format IN ('zip', 'dump')),
                filestore_requested INTEGER NOT NULL CHECK (filestore_requested IN (0, 1)),
                path TEXT,
                filename TEXT,
                size_bytes INTEGER,
                sha256 TEXT,
                state TEXT NOT NULL CHECK (state IN ('downloading', 'available', 'failed', 'deleted')),
                started_at TEXT NOT NULL,
                downloaded_at TEXT,
                failed_at TEXT,
                deleted_at TEXT,
                error_type TEXT,
                error_message TEXT,
                source_git_branch TEXT
            );
            CREATE INDEX IF NOT EXISTS backups_lookup_idx ON backups (source_base_url, database_name, downloaded_at DESC);
            CREATE INDEX IF NOT EXISTS backups_state_idx ON backups (state);

            CREATE TABLE IF NOT EXISTS backup_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_id TEXT NOT NULL REFERENCES backups(id),
                event_type TEXT NOT NULL CHECK (event_type IN ('download_started', 'download_succeeded', 'download_failed', 'validation_succeeded', 'validation_failed', 'validation_unavailable', 'deleted')),
                occurred_at TEXT NOT NULL,
                path TEXT,
                validator TEXT,
                exit_code INTEGER,
                message TEXT
            );
            CREATE INDEX IF NOT EXISTS backup_events_backup_idx ON backup_events (backup_id, sequence DESC);
        """)
        conn.commit()

        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        self._run_migrations(conn, user_version)

    def _run_migrations(self, conn: sqlite3.Connection, user_version: int) -> None:  # noqa: C901
        if user_version < 2:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS restores (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_host TEXT NOT NULL,
                    db_port INTEGER NOT NULL,
                    database_name TEXT NOT NULL,
                    backup_id TEXT NOT NULL REFERENCES backups(id),
                    restored_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS restores_cluster_idx ON restores (db_host, db_port, database_name, restored_at DESC);

                CREATE TABLE IF NOT EXISTS database_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_host TEXT NOT NULL,
                    db_port INTEGER NOT NULL,
                    database_name TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK (event_type IN ('restored', 'dropped')),
                    occurred_at TEXT NOT NULL,
                    backup_id TEXT,
                    CHECK (event_type = 'dropped' OR backup_id IS NOT NULL),
                    FOREIGN KEY (backup_id) REFERENCES backups(id)
                );
                CREATE INDEX IF NOT EXISTS database_events_cluster_idx ON database_events (db_host, db_port, database_name, sequence DESC);
            """)
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
        if user_version < 3:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS environments (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    repository_root TEXT NOT NULL,
                    git_common_dir TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    base_ref TEXT NOT NULL,
                    worktree_path TEXT NOT NULL,
                    generated_config_path TEXT NOT NULL,
                    python_environment_path TEXT NOT NULL,
                    python_environment_owned INTEGER NOT NULL,
                    dependency_lock_path TEXT NOT NULL,
                    http_interface TEXT NOT NULL,
                    http_port INTEGER NOT NULL,
                    db_mode TEXT NOT NULL,
                    source_db_name TEXT,
                    target_db_name TEXT,
                    backup_id TEXT,
                    runtime_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    removed_at TEXT,
                    last_error TEXT,
                    FOREIGN KEY (backup_id) REFERENCES backups(id)
                );
                CREATE INDEX IF NOT EXISTS environments_active_idx ON environments (git_common_dir, branch, state);
                CREATE INDEX IF NOT EXISTS environments_port_idx ON environments (http_port, state);

                CREATE TABLE IF NOT EXISTS environment_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    environment_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK (operation IN ('checkout', 'sync', 'use', 'shell', 'remove')),
                    outcome TEXT NOT NULL CHECK (outcome IN ('started', 'succeeded', 'failed')),
                    occurred_at TEXT NOT NULL,
                    message TEXT,
                    FOREIGN KEY (environment_id) REFERENCES environments(id)
                );
                CREATE INDEX IF NOT EXISTS environment_events_env_idx ON environment_events (environment_id, sequence DESC);
            """)
            conn.execute("PRAGMA user_version = 3")
            conn.commit()
            user_version = 3
        if user_version < 4:
            # More than one non-removed environment for a checkout would make
            # ownership and cleanup ambiguous.  Older catalogs may contain
            # such rows; retain the newest one and mark older rows removed
            # before installing the invariant.
            conn.execute(
                """UPDATE environments SET state='removed', removed_at=datetime('now'), last_error='superseded during active-environment migration'
                   WHERE id IN (
                     SELECT id FROM (
                       SELECT id, ROW_NUMBER() OVER (
                         PARTITION BY git_common_dir, branch
                         ORDER BY created_at DESC, id DESC
                       ) AS position
                       FROM environments WHERE state <> 'removed'
                     ) WHERE position > 1
                   )"""
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS environments_one_active_branch "
                "ON environments(git_common_dir, branch) WHERE state <> 'removed'"
            )
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
            user_version = 4
        if user_version < 5:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS environment_copy_journal (
                    environment_id TEXT PRIMARY KEY REFERENCES environments(id),
                    target_database TEXT NOT NULL,
                    db_host TEXT NOT NULL,
                    db_port INTEGER NOT NULL,
                    db_user TEXT,
                    backup_id TEXT REFERENCES backups(id),
                    stage TEXT NOT NULL CHECK (stage IN ('prepared', 'backed_up', 'restored', 'dropped', 'backup_deleted')),
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("PRAGMA user_version = 5")
            conn.commit()
            user_version = 5
        if user_version < 6:
            conn.executescript("""
                ALTER TABLE environment_copy_journal RENAME TO environment_copy_journal_v5;
                CREATE TABLE environment_copy_journal (
                    environment_id TEXT PRIMARY KEY REFERENCES environments(id),
                    target_database TEXT NOT NULL,
                    db_host TEXT NOT NULL,
                    db_port INTEGER NOT NULL,
                    db_user TEXT,
                    backup_id TEXT REFERENCES backups(id),
                    stage TEXT NOT NULL CHECK (stage IN ('prepared', 'backed_up', 'restore_pending', 'restored', 'dropped', 'backup_deleted')),
                    updated_at TEXT NOT NULL
                );
                INSERT INTO environment_copy_journal
                    SELECT environment_id, target_database, db_host, db_port, db_user, backup_id, stage, updated_at
                    FROM environment_copy_journal_v5;
                DROP TABLE environment_copy_journal_v5;
            """)
            conn.execute("PRAGMA user_version = 6")
            conn.commit()
            user_version = 6
        if user_version < 7:
            self._migrate_v7_one_active_port(conn)
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
            user_version = 7
        if user_version < 8:
            self._migrate_v8_drop_http_port(conn)
            conn.execute("PRAGMA user_version = 8")
            conn.commit()
            user_version = 8
        if user_version < 9:
            self._migrate_v9_environment_runtime(conn)
            conn.execute("PRAGMA user_version = 9")
            conn.commit()
            user_version = 9
        if user_version < 10:
            self._migrate_v10_backup_source_branch(conn)
            conn.execute("PRAGMA user_version = 10")
            conn.commit()
            user_version = 10
        if user_version < 11:
            self._migrate_v11_project_runtime_ownership(conn)
            conn.execute("PRAGMA user_version = 11")
            conn.commit()

    def _migrate_v10_backup_source_branch(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(backups)")}
        if "source_git_branch" not in columns:
            conn.execute("ALTER TABLE backups ADD COLUMN source_git_branch TEXT")

    def _migrate_v9_environment_runtime(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS environment_runtime (
                environment_id TEXT PRIMARY KEY REFERENCES environments(id),
                root_pid INTEGER NOT NULL,
                create_time REAL NOT NULL,
                started_at TEXT NOT NULL,
                checkout_branch TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                http_url TEXT NOT NULL,
                http_port INTEGER NOT NULL,
                database_name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

    def _migrate_v11_project_runtime_ownership(  # noqa: C901
        self, conn: sqlite3.Connection
    ) -> None:
        """Install project registration and migrate runtime ownership atomically.

        The old ``environment_runtime`` table is retained only as a read-only
        compatibility view.  Runtime writes go through the polymorphic table,
        whose owner columns are deliberately non-null and constrained to the
        two supported owner kinds.
        """
        with conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    repository_root TEXT NOT NULL,
                    git_common_dir TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS projects_identity_idx ON projects(repository_root, git_common_dir)"
            )

            # Backfill canonical registrations before moving runtime rows so
            # every migrated environment owner has a project to join.
            environment_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(environments)")
            }
            environments = (
                conn.execute(
                    "SELECT repository_root, git_common_dir FROM environments "
                    "GROUP BY repository_root, git_common_dir"
                ).fetchall()
                if {"repository_root", "git_common_dir"} <= environment_columns
                else []
            )
            for row in environments:
                repository_root = Path(str(row["repository_root"])).resolve()
                git_common_dir = Path(str(row["git_common_dir"])).resolve()
                project_id = f"project_{repo_key(repository_root, git_common_dir)}"
                conn.execute(
                    """INSERT INTO projects
                       (project_id, repository_root, git_common_dir, registered_at, updated_at)
                       VALUES (?, ?, ?, datetime('now'), datetime('now'))
                       ON CONFLICT(project_id) DO UPDATE SET
                         repository_root=excluded.repository_root,
                         git_common_dir=excluded.git_common_dir,
                         updated_at=excluded.updated_at""",
                    (project_id, str(repository_root), str(git_common_dir)),
                )

            runtime_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime'"
            ).fetchone()
            legacy_runtime_name: str | None = None
            if runtime_exists is not None:
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(runtime)")}
                if {"environment_id", "project_id"} <= columns:
                    for row in conn.execute("SELECT environment_id, project_id FROM runtime"):
                        environment_owner = str(row[0]).strip() if row[0] is not None else ""
                        project_owner = str(row[1]).strip() if row[1] is not None else ""
                        if bool(environment_owner) == bool(project_owner):
                            raise BackupCatalogError(
                                "catalog runtime row must have exactly one owner"
                            )
                if {"owner_kind", "owner_id"} <= columns:
                    for row in conn.execute("SELECT owner_kind, owner_id FROM runtime"):
                        if (
                            row[0] not in {"environment", "project"}
                            or row[1] is None
                            or not str(row[1]).strip()
                        ):
                            raise BackupCatalogError(
                                "catalog runtime row must have exactly one valid owner"
                            )
                elif not {"environment_id", "project_id"} <= columns:
                    raise BackupCatalogError("catalog runtime table has an unsupported shape")
                legacy_runtime_name = "runtime_legacy_v10"
                conn.execute("ALTER TABLE runtime RENAME TO runtime_legacy_v10")

            conn.execute(
                """CREATE TABLE IF NOT EXISTS runtime (
                    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('environment', 'project')),
                    owner_id TEXT NOT NULL CHECK (length(trim(owner_id)) > 0),
                    root_pid INTEGER NOT NULL,
                    create_time REAL NOT NULL,
                    started_at TEXT NOT NULL,
                    checkout_branch TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    http_url TEXT NOT NULL,
                    http_port INTEGER NOT NULL,
                    database_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (owner_kind, owner_id)
                )"""
            )

            if legacy_runtime_name is not None:
                columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(runtime_legacy_v10)")
                }
                if {"owner_kind", "owner_id"} <= columns:
                    conn.execute(
                        """INSERT INTO runtime
                           SELECT owner_kind, owner_id, root_pid, create_time, started_at,
                                  checkout_branch, commit_sha, http_url, http_port,
                                  database_name, updated_at
                           FROM runtime_legacy_v10"""
                    )
                else:
                    conn.execute(
                        """INSERT INTO runtime
                           SELECT CASE WHEN environment_id IS NOT NULL THEN 'environment' ELSE 'project' END,
                                  COALESCE(environment_id, project_id), root_pid, create_time,
                                  started_at, checkout_branch, commit_sha, http_url, http_port,
                                  database_name, updated_at
                           FROM runtime_legacy_v10"""
                    )
                conn.execute("DROP TABLE runtime_legacy_v10")

            legacy_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='environment_runtime'"
            ).fetchone()
            if legacy_exists is not None:
                legacy_columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(environment_runtime)")
                }
                if {
                    "environment_id",
                    "root_pid",
                    "create_time",
                    "started_at",
                    "checkout_branch",
                    "commit_sha",
                    "http_url",
                    "http_port",
                    "database_name",
                    "updated_at",
                } <= legacy_columns:
                    for row in conn.execute("SELECT environment_id FROM environment_runtime"):
                        if row[0] is None or not str(row[0]).strip():
                            raise BackupCatalogError(
                                "catalog runtime row must have exactly one owner"
                            )
                    conn.execute(
                        """INSERT INTO runtime
                           (owner_kind, owner_id, root_pid, create_time, started_at,
                            checkout_branch, commit_sha, http_url, http_port, database_name, updated_at)
                           SELECT 'environment', environment_id, root_pid, create_time, started_at,
                                  checkout_branch, commit_sha, http_url, http_port, database_name, updated_at
                           FROM environment_runtime"""
                    )
                conn.execute("DROP TABLE environment_runtime")

            conn.execute(
                """CREATE VIEW environment_runtime AS
                   SELECT owner_id AS environment_id, root_pid, create_time, started_at,
                          checkout_branch, commit_sha, http_url, http_port, database_name, updated_at
                   FROM runtime WHERE owner_kind = 'environment'"""
            )

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

    def _list_monitor_projects(self) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        return self._last_monitor_projects, self._last_monitor_project_runtimes

    def _migrate_v7_one_active_port(self, conn: sqlite3.Connection) -> None:
        # A port is a global host resource.  Never silently mark a live
        # environment removed merely to make an index creation succeed.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(environments)")}
        if {"state", "http_port", "created_at"} <= columns:
            duplicate = conn.execute(
                "SELECT http_port FROM environments WHERE state <> 'removed' "
                "GROUP BY http_port HAVING COUNT(*) > 1 LIMIT 1"
            ).fetchone()
            if duplicate is not None:
                raise BackupCatalogError(
                    f"catalog has multiple active environments reserving port {duplicate[0]}; "
                    "resolve the conflict before upgrading"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS environments_one_active_port "
                "ON environments(http_port) WHERE state <> 'removed'"
            )

    def _migrate_v8_drop_http_port(self, conn: sqlite3.Connection) -> None:
        """Drop catalog HTTP columns; generated odoo.conf is the source of truth.

        SQLite older than 3.35 has no DROP COLUMN, so the table is recreated.
        ``legacy_alter_table`` plus ``foreign_keys=OFF`` keeps child-table
        foreign keys pointed at ``environments`` across the rename.
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(environments)")}
        if "http_port" not in columns and "http_interface" not in columns:
            return
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")
        try:
            conn.executescript(
                """
                ALTER TABLE environments RENAME TO environments_v7;
                CREATE TABLE environments (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    repository_root TEXT NOT NULL,
                    git_common_dir TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    base_ref TEXT NOT NULL,
                    worktree_path TEXT NOT NULL,
                    generated_config_path TEXT NOT NULL,
                    python_environment_path TEXT NOT NULL,
                    python_environment_owned INTEGER NOT NULL,
                    dependency_lock_path TEXT NOT NULL,
                    db_mode TEXT NOT NULL,
                    source_db_name TEXT,
                    target_db_name TEXT,
                    backup_id TEXT,
                    runtime_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    removed_at TEXT,
                    last_error TEXT,
                    FOREIGN KEY (backup_id) REFERENCES backups(id)
                );
                INSERT INTO environments
                    SELECT id, name, repository_root, git_common_dir, branch, base_ref,
                           worktree_path, generated_config_path, python_environment_path,
                           python_environment_owned, dependency_lock_path, db_mode,
                           source_db_name, target_db_name, backup_id, runtime_json,
                           state, created_at, last_used_at, removed_at, last_error
                    FROM environments_v7;
                DROP TABLE environments_v7;
                CREATE INDEX IF NOT EXISTS environments_active_idx
                    ON environments (git_common_dir, branch, state);
                CREATE UNIQUE INDEX IF NOT EXISTS environments_one_active_branch
                    ON environments(git_common_dir, branch) WHERE state <> 'removed';
                """
            )
        finally:
            conn.execute("PRAGMA legacy_alter_table=OFF")
            conn.execute("PRAGMA foreign_keys=ON")

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
    ) -> None:
        self._conn.execute(
            "INSERT INTO backups (id, source_base_url, database_name, format, filestore_requested, path, state, started_at, source_git_branch) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)",
            (
                backup_id,
                source_base_url,
                database_name,
                format,
                int(filestore_requested),
                str(path),
                BackupState.DOWNLOADING.value,
                source_git_branch,
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
    ) -> list[Backup]:
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
    ) -> Backup | None:
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
        query += " ORDER BY downloaded_at DESC, id DESC LIMIT 1"
        row = self._conn.execute(query, params).fetchone()
        if row is None:
            return None
        return _row_to_backup(row)

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
            ("database_name", row["database_name"], backup.database_name),
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

    @_translate_sqlite_error
    def record_restore(
        self,
        db_host: str | None,
        db_port: int,
        database_name: str,
        backup_id: str,
    ) -> None:
        host = normalize_db_host(db_host)
        self._conn.execute(
            "INSERT INTO restores (db_host, db_port, database_name, backup_id, restored_at) VALUES (?, ?, ?, ?, datetime('now'))",
            (host, db_port, database_name, backup_id),
        )
        self._conn.execute(
            "INSERT INTO database_events (db_host, db_port, database_name, event_type, occurred_at, backup_id) VALUES (?, ?, ?, 'restored', datetime('now'), ?)",
            (host, db_port, database_name, backup_id),
        )
        self._conn.commit()

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

    @_translate_sqlite_error
    def create_environment(self, env: Mapping[str, CatalogValue]) -> None:
        self._conn.execute(
            """INSERT INTO environments (
                id, name, repository_root, git_common_dir, branch, base_ref,
                worktree_path, generated_config_path, python_environment_path,
                python_environment_owned, dependency_lock_path, db_mode,
                source_db_name, target_db_name, backup_id,
                runtime_json, state, created_at, last_used_at, removed_at, last_error
            ) VALUES (
                :id, :name, :repository_root, :git_common_dir, :branch, :base_ref,
                :worktree_path, :generated_config_path, :python_environment_path,
                :python_environment_owned, :dependency_lock_path, :db_mode,
                :source_db_name, :target_db_name, :backup_id,
                :runtime_json, :state, :created_at, :last_used_at, :removed_at, :last_error
            )""",
            {
                "id": env["id"],
                "name": env["name"],
                "repository_root": env["repository_root"],
                "git_common_dir": env["git_common_dir"],
                "branch": env["branch"],
                "base_ref": env["base_ref"],
                "worktree_path": env["worktree_path"],
                "generated_config_path": env["generated_config_path"],
                "python_environment_path": env["python_environment_path"],
                "python_environment_owned": int(bool(env["python_environment_owned"])),
                "dependency_lock_path": env["dependency_lock_path"],
                "db_mode": env["db_mode"],
                "source_db_name": env.get("source_db_name"),
                "target_db_name": env.get("target_db_name"),
                "backup_id": env.get("backup_id"),
                "runtime_json": env["runtime_json"],
                "state": env["state"],
                "created_at": env["created_at"],
                "last_used_at": env.get("last_used_at"),
                "removed_at": env.get("removed_at"),
                "last_error": sanitize_last_error(str(env.get("last_error")))
                if env.get("last_error")
                else None,
            },
        )
        self._conn.commit()

    @_translate_sqlite_error
    def update_environment_state(
        self,
        environment_id: str,
        state: str,
        *,
        last_error: str | None = None,
        removed_at: str | None = None,
    ) -> None:
        sets: list[str] = ["state = ?"]
        params: list[str | None] = [state]
        if last_error is not None:
            sets.append("last_error = ?")
            params.append(sanitize_last_error(last_error))
        if removed_at is not None:
            sets.append("removed_at = ?")
            params.append(removed_at)
        params.append(environment_id)
        self._conn.execute(
            f"UPDATE environments SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        self._conn.commit()

    @_translate_sqlite_error
    def update_environment(
        self, environment_id: str, fields_map: Mapping[str, CatalogValue]
    ) -> None:
        if not fields_map:
            return
        mutable_fields = dict(fields_map)
        if "last_error" in mutable_fields and mutable_fields["last_error"] is not None:
            mutable_fields["last_error"] = sanitize_last_error(str(mutable_fields["last_error"]))
        cols = ", ".join(f"{c} = :{c}" for c in mutable_fields)
        params = dict(mutable_fields, id=environment_id)
        self._conn.execute(
            f"UPDATE environments SET {cols} WHERE id = :id",
            params,
        )
        self._conn.commit()

    @_translate_sqlite_error
    def record_environment_use(self, environment_id: str, last_used_at: str) -> None:
        """Persist the use timestamp and success event as one transaction."""
        with self._conn:
            self._conn.execute(
                "UPDATE environments SET last_used_at = ? WHERE id = ?",
                (last_used_at, environment_id),
            )
            self._conn.execute(
                "INSERT INTO environment_events (environment_id, operation, outcome, occurred_at, message) "
                "VALUES (?, ?, ?, datetime('now'), ?)",
                (environment_id, "use", "succeeded", None),
            )

    @_translate_sqlite_error
    def get_environment(self, environment_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM environments WHERE id = ?",
            (environment_id,),
        ).fetchone()
        return row

    @_translate_sqlite_error
    def list_environments(
        self,
        *,
        git_common_dir: str | None = None,
        include_removed: bool = False,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM environments"
        params: list[str] = []
        clauses: list[str] = []
        if git_common_dir is not None:
            clauses.append("git_common_dir = ?")
            params.append(git_common_dir)
        if not include_removed:
            clauses.append("state != 'removed'")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, id DESC"
        return self._conn.execute(query, params).fetchall()

    @_translate_sqlite_error
    def upsert_copy_journal(
        self,
        environment_id: str,
        *,
        target_database: str,
        db_host: str | None,
        db_port: int,
        db_user: str | None,
        backup_id: str | None,
        stage: CopyJournalStage,
    ) -> None:
        if not isinstance(stage, CopyJournalStage):
            raise TypeError("copy journal stage must be a CopyJournalStage")
        self._conn.execute(
            """INSERT INTO environment_copy_journal
               (environment_id,target_database,db_host,db_port,db_user,backup_id,stage,updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(environment_id) DO UPDATE SET
                 target_database=excluded.target_database, db_host=excluded.db_host,
                 db_port=excluded.db_port, db_user=excluded.db_user,
                 backup_id=excluded.backup_id, stage=excluded.stage, updated_at=excluded.updated_at""",
            (
                environment_id,
                target_database,
                normalize_db_host(db_host),
                db_port,
                db_user,
                backup_id,
                stage.value,
            ),
        )
        self._conn.commit()

    @_translate_sqlite_error
    def get_copy_journal(self, environment_id: str) -> sqlite3.Row | None:
        return cast(
            "sqlite3.Row | None",
            self._conn.execute(
                "SELECT * FROM environment_copy_journal WHERE environment_id=?", (environment_id,)
            ).fetchone(),
        )

    @_translate_sqlite_error
    def add_environment_event(
        self,
        environment_id: str,
        operation: str,
        outcome: str,
        *,
        message: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO environment_events (environment_id, operation, outcome, occurred_at, message) "
            "VALUES (?, ?, ?, datetime('now'), ?)",
            (environment_id, operation, outcome, sanitize_event_message(message)),
        )
        self._conn.commit()

    @_translate_sqlite_error
    def active_environment_for(self, git_common_dir: str, branch: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM environments WHERE git_common_dir = ? AND branch = ? "
            "AND state NOT IN ('removed') ORDER BY created_at DESC LIMIT 1",
            (git_common_dir, branch),
        ).fetchone()
        return row

    @_translate_sqlite_error
    def get_environment_runtime(self, environment_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM environment_runtime WHERE environment_id = ?",
            (environment_id,),
        ).fetchone()
        return row

    @_translate_sqlite_error
    def list_environment_runtimes(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM environment_runtime ORDER BY environment_id"
        ).fetchall()

    @_translate_sqlite_error
    def list_environments_with_runtimes(
        self, *, include_removed: bool = False
    ) -> list[tuple[sqlite3.Row, sqlite3.Row | None]]:
        """Read monitor rows, backup metadata, and runtime identities atomically.

        The private project rows captured during this same transaction are
        available to the monitor through ``_list_monitor_projects``.  The
        return value intentionally remains the historic environment-only
        shape for compatibility.
        """
        self._conn.execute("BEGIN")
        try:
            state_clause = "" if include_removed else " WHERE e.state != 'removed'"
            environments = self._conn.execute(
                "SELECT e.*, b.state AS backup_state, b.path AS backup_path "
                "FROM environments e LEFT JOIN backups b ON b.id = e.backup_id"
                f"{state_clause} ORDER BY e.created_at DESC, e.id DESC"
            ).fetchall()
            runtimes = self._conn.execute(
                "SELECT * FROM runtime WHERE owner_kind = 'environment' ORDER BY owner_id"
            ).fetchall()
            self._last_monitor_projects = self._conn.execute(
                "SELECT * FROM projects ORDER BY project_id"
            ).fetchall()
            self._last_monitor_project_runtimes = self._conn.execute(
                "SELECT * FROM runtime WHERE owner_kind = 'project' ORDER BY owner_id"
            ).fetchall()
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        by_environment = {str(runtime["owner_id"]): runtime for runtime in runtimes}
        return [(row, by_environment.get(str(row["id"]))) for row in environments]

    @_translate_sqlite_error
    def _upsert_runtime(
        self,
        owner_kind: str,
        owner_id: str,
        *,
        root_pid: int,
        create_time: float,
        started_at: str,
        checkout_branch: str,
        commit_sha: str,
        http_url: str,
        http_port: int,
        database_name: str,
    ) -> None:
        if owner_kind not in {"environment", "project"} or not owner_id.strip():
            raise BackupCatalogError("runtime owner must be exactly environment or project")
        if owner_kind == "environment":
            if self.get_environment(owner_id) is None:
                raise BackupCatalogError(f"runtime environment does not exist: {owner_id}")
        else:
            registered = self._conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (owner_id,)
            ).fetchone()
            if registered is None:
                raise BackupCatalogError(f"runtime project is not registered: {owner_id}")
        self._conn.execute(
            """INSERT INTO runtime
               (owner_kind, owner_id, root_pid, create_time, started_at, checkout_branch,
                commit_sha, http_url, http_port, database_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(owner_kind, owner_id) DO UPDATE SET
                 root_pid=excluded.root_pid, create_time=excluded.create_time,
                 started_at=excluded.started_at, checkout_branch=excluded.checkout_branch,
                 commit_sha=excluded.commit_sha, http_url=excluded.http_url,
                 http_port=excluded.http_port, database_name=excluded.database_name,
                 updated_at=excluded.updated_at""",
            (
                owner_kind,
                owner_id,
                root_pid,
                create_time,
                started_at,
                checkout_branch,
                commit_sha,
                http_url,
                http_port,
                database_name,
            ),
        )
        self._conn.commit()

    @_translate_sqlite_error
    def _clear_runtime(self, owner_kind: str, owner_id: str) -> None:
        if owner_kind not in {"environment", "project"}:
            raise BackupCatalogError("runtime owner must be exactly environment or project")
        self._conn.execute(
            "DELETE FROM runtime WHERE owner_kind = ? AND owner_id = ?",
            (owner_kind, owner_id),
        )
        self._conn.commit()

    @_translate_sqlite_error
    def upsert_environment_runtime(
        self,
        environment_id: str,
        *,
        root_pid: int,
        create_time: float,
        started_at: str,
        checkout_branch: str,
        commit_sha: str,
        http_url: str,
        http_port: int,
        database_name: str,
    ) -> None:
        self._upsert_runtime(
            "environment",
            environment_id,
            root_pid=root_pid,
            create_time=create_time,
            started_at=started_at,
            checkout_branch=checkout_branch,
            commit_sha=commit_sha,
            http_url=http_url,
            http_port=http_port,
            database_name=database_name,
        )

    @_translate_sqlite_error
    def clear_environment_runtime(self, environment_id: str) -> None:
        self._clear_runtime("environment", environment_id)

    def _add_event(
        self,
        backup_id: str,
        event_type: str,
        path: str | None = None,
        validator: str | None = None,
        exit_code: int | None = None,
        message: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO backup_events (backup_id, event_type, occurred_at, path, validator, exit_code, message) VALUES (?, ?, datetime('now'), ?, ?, ?, ?)",
            (
                backup_id,
                event_type,
                path,
                validator,
                exit_code,
                message,
            ),
        )


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
