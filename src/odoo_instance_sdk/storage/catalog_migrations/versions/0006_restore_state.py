"""Track whether a restore binding completed successfully."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in bind.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade() -> None:
    """Backfill existing restore bindings as complete."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if _has_column(bind, "restores", "state"):
        return
    bind.exec_driver_sql(
        "ALTER TABLE restores ADD COLUMN state TEXT NOT NULL DEFAULT 'complete' "
        "CHECK (state IN ('complete', 'incomplete'))"
    )


def downgrade() -> None:
    """Remove incomplete-state tracking using the legacy complete-only shape."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind, "restores", "state"):
        return
    bind.exec_driver_sql(
        """CREATE TABLE restores__old (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            backup_id TEXT REFERENCES backups(id),
            source_kind TEXT NOT NULL CHECK (source_kind IN ('catalogue', 'local_archive')),
            source_sha256 TEXT,
            restored_at TEXT NOT NULL,
            cluster_id TEXT,
            data_directory TEXT,
            CHECK (
                (source_kind = 'catalogue' AND backup_id IS NOT NULL AND source_sha256 IS NULL)
                OR (source_kind = 'local_archive' AND backup_id IS NULL AND source_sha256 IS NOT NULL
                    AND length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*')
            )
        )"""
    )
    bind.exec_driver_sql(
        """INSERT INTO restores__old
           (sequence, db_host, db_port, database_name, backup_id, source_kind,
            source_sha256, restored_at, cluster_id, data_directory)
           SELECT sequence, db_host, db_port, database_name, backup_id, source_kind,
                  source_sha256, restored_at, cluster_id, data_directory
           FROM restores"""
    )
    bind.exec_driver_sql("DROP TABLE restores")
    bind.exec_driver_sql("ALTER TABLE restores__old RENAME TO restores")
    bind.exec_driver_sql(
        "CREATE INDEX restores_cluster_idx ON restores "
        "(db_host, db_port, database_name, restored_at DESC)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX restores_cluster_identity_idx ON restores "
        "(cluster_id, db_host, db_port, database_name, restored_at DESC)"
    )
