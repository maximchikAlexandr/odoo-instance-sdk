from __future__ import annotations

# ruff: noqa: F821
import sqlite3
from collections.abc import Mapping
from typing import cast

from odoo_instance_sdk.exceptions import (
    BackupCatalogError,
)
from odoo_instance_sdk.internal.applied_settings import (
    LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON,
    AppliedSettingsError,
    decode_applied_settings,
)
from odoo_instance_sdk.internal.sanitize import sanitize_event_message, sanitize_last_error
from odoo_instance_sdk.storage.catalog import helpers as _helpers
from odoo_instance_sdk.storage.catalog.helpers import (
    CatalogValue as CatalogValue,
)
from odoo_instance_sdk.storage.catalog.helpers import (
    _translate_sqlite_error as _translate_sqlite_error,
)

globals().update(
    {name: value for name, value in _helpers.__dict__.items() if not name.startswith("__")}
)


class _EnvironmentMixin:
    @_translate_sqlite_error
    def create_environment(self, env: Mapping[str, CatalogValue]) -> None:
        applied_settings = env.get("applied_settings_json", LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON)
        if not isinstance(applied_settings, str):
            raise BackupCatalogError("invalid applied_settings_json")
        try:
            decode_applied_settings(applied_settings)
        except AppliedSettingsError as exc:
            raise BackupCatalogError("invalid applied_settings_json") from exc
        self._conn.execute(
            """INSERT INTO environments (
                id, name, repository_root, git_common_dir, branch, base_ref,
                worktree_path, generated_config_path, python_environment_path,
                python_environment_owned, dependency_lock_path, db_mode,
                source_db_name, target_db_name, backup_id,
                runtime_json, applied_settings_json, state, created_at, last_used_at,
                removed_at, last_error
            ) VALUES (
                :id, :name, :repository_root, :git_common_dir, :branch, :base_ref,
                :worktree_path, :generated_config_path, :python_environment_path,
                :python_environment_owned, :dependency_lock_path, :db_mode,
                :source_db_name, :target_db_name, :backup_id,
                :runtime_json, :applied_settings_json, :state, :created_at, :last_used_at,
                :removed_at, :last_error
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
                "applied_settings_json": applied_settings,
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
    def _finalize_environment_checkout(
        self, environment_id: str, applied_settings_json: str
    ) -> None:
        """Atomically publish applied evidence, ready state, and success event."""
        if not isinstance(applied_settings_json, str):
            raise BackupCatalogError("invalid applied_settings_json")
        try:
            decode_applied_settings(applied_settings_json)
        except AppliedSettingsError as exc:
            raise BackupCatalogError("invalid applied_settings_json") from exc
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE environments SET state = ?, applied_settings_json = ? WHERE id = ?",
                ("ready", applied_settings_json, environment_id),
            )
            if cursor.rowcount != 1:
                raise BackupCatalogError("environment row disappeared during checkout finalization")
            self._conn.execute(
                "INSERT INTO environment_events "
                "(environment_id, operation, outcome, occurred_at, message) "
                "VALUES (?, 'checkout', 'succeeded', datetime('now'), NULL)",
                (environment_id,),
            )

    @_translate_sqlite_error
    def _record_environment_sync_success(
        self, environment_id: str, applied_settings_json: str
    ) -> None:
        """Atomically publish sync evidence and its successful lifecycle event."""
        if not isinstance(applied_settings_json, str):
            raise BackupCatalogError("invalid applied_settings_json")
        try:
            decode_applied_settings(applied_settings_json)
        except AppliedSettingsError as exc:
            raise BackupCatalogError("invalid applied_settings_json") from exc
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE environments SET applied_settings_json = ? WHERE id = ?",
                (applied_settings_json, environment_id),
            )
            if cursor.rowcount != 1:
                raise BackupCatalogError("environment row disappeared during sync finalization")
            self._conn.execute(
                "INSERT INTO environment_events "
                "(environment_id, operation, outcome, occurred_at, message) "
                "VALUES (?, 'sync', 'succeeded', datetime('now'), NULL)",
                (environment_id,),
            )

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
        """Return the historic environment-only projection of the monitor read."""
        return list(self._monitor_snapshot_rows(include_removed=include_removed).environments)

    @_translate_sqlite_error
    def _monitor_snapshot_rows(
        self, *, include_removed: bool = False, project_id: str | None = None
    ) -> MonitorCatalogSnapshot:
        """Read all monitor catalog inputs in one transactionally typed snapshot."""
        self._conn.execute("BEGIN")
        try:
            clauses: list[str] = []
            params: list[str] = []
            if not include_removed:
                clauses.append("e.state != 'removed'")
            if project_id is not None:
                clauses.append(
                    "EXISTS ("
                    "SELECT 1 FROM projects p "
                    "WHERE p.project_id = ? AND p.repository_root = e.repository_root "
                    "AND p.git_common_dir = e.git_common_dir"
                    ")"
                )
                params.append(project_id)
            state_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
            environment_query = (
                "SELECT e.*, b.state AS backup_state, b.path AS backup_path "
                "FROM environments e LEFT JOIN backups b ON b.id = e.backup_id"
                f"{state_clause} ORDER BY e.created_at DESC, e.id DESC"
            )
            environments = (
                self._conn.execute(environment_query, params).fetchall()
                if params
                else self._conn.execute(environment_query).fetchall()
            )
            if project_id is None:
                runtimes = self._conn.execute(
                    "SELECT * FROM runtime WHERE owner_kind = 'environment' ORDER BY owner_id"
                ).fetchall()
                projects = self._conn.execute(
                    "SELECT * FROM projects ORDER BY project_id"
                ).fetchall()
                project_runtimes = self._conn.execute(
                    "SELECT * FROM runtime WHERE owner_kind = 'project' ORDER BY owner_id"
                ).fetchall()
            else:
                environment_ids = [str(row["id"]) for row in environments]
                if environment_ids:
                    placeholders = ",".join("?" for _ in environment_ids)
                    runtimes = self._conn.execute(
                        "SELECT * FROM runtime WHERE owner_kind = 'environment' "
                        f"AND owner_id IN ({placeholders}) ORDER BY owner_id",
                        environment_ids,
                    ).fetchall()
                else:
                    runtimes = []
                projects = self._conn.execute(
                    "SELECT * FROM projects WHERE project_id = ? ORDER BY project_id",
                    (project_id,),
                ).fetchall()
                project_runtimes = self._conn.execute(
                    "SELECT * FROM runtime WHERE owner_kind = 'project' AND owner_id = ?",
                    (project_id,),
                ).fetchall()
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        by_environment = {str(runtime["owner_id"]): runtime for runtime in runtimes}
        return MonitorCatalogSnapshot(
            environments=tuple((row, by_environment.get(str(row["id"]))) for row in environments),
            projects=tuple(projects),
            project_runtimes=tuple(project_runtimes),
        )

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

    @_translate_sqlite_error
    def _clear_environment_runtime_if_matches(
        self, environment_id: str, *, root_pid: int, create_time: float
    ) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM runtime WHERE owner_kind = 'environment' AND owner_id = ? "
            "AND root_pid = ? AND create_time = ?",
            (environment_id, root_pid, create_time),
        )
        self._conn.commit()
        return cursor.rowcount == 1

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
