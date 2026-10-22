from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from odoo_instance_sdk.exceptions import BackupCatalogError
from odoo_instance_sdk.storage.catalog.helpers import (
    PostgresClusterClaim as PostgresClusterClaim,
    resolve_postgres_cluster_claim as resolve_postgres_cluster_claim,
    _row_to_cluster_claim as _row_to_cluster_claim,
    _translate_sqlite_error as _translate_sqlite_error,
)


class _PostgresClusterMixin:
    if TYPE_CHECKING:
        _conn: sqlite3.Connection

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
    def _get_postgres_cluster_for_project(
        self,
        project_id: str,
        repository_root: str | Path,
        git_common_dir: str | Path | None,
    ) -> PostgresClusterClaim | None:
        return resolve_postgres_cluster_claim(
            self._get_postgres_cluster,
            project_id,
            repository_root,
            git_common_dir,
        )

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
    def _remove_postgres_cluster_claim(
        self,
        cluster_id: uuid.UUID | str,
        project_id: str,
        compose_project: str,
        volume_name: str,
    ) -> bool:
        """Forget one exact active claim after its owned Compose resources are absent."""
        identifier = self._cluster_uuid(cluster_id)
        project = self._cluster_text(project_id, "project_id")
        compose = self._cluster_text(compose_project, "compose_project")
        volume = self._cluster_text(volume_name, "volume_name")
        with self._conn:
            cursor = self._conn.execute(
                """DELETE FROM postgres_clusters
                   WHERE cluster_id = ? AND project_id = ? AND compose_project = ?
                     AND volume_name = ? AND state = 'active'""",
                (identifier, project, compose, volume),
            )
            return cursor.rowcount == 1
