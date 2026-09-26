"""Add constrained source-neutral restore provenance."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in bind.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade() -> None:
    """Backfill existing rows as catalogue provenance and add local evidence."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if _has_column(bind, "restores", "source_kind"):
        return

    # Rebuild only the two affected tables.  Raw SQLite DDL avoids reflecting
    # unrelated expression indexes under strict warning-as-error test settings.
    bind.exec_driver_sql(
        """CREATE TABLE restores__new (
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
        """INSERT INTO restores__new
           (sequence, db_host, db_port, database_name, backup_id, source_kind,
            source_sha256, restored_at, cluster_id, data_directory)
           SELECT sequence, db_host, db_port, database_name, backup_id, 'catalogue',
                  NULL, restored_at, cluster_id, data_directory
           FROM restores"""
    )
    bind.exec_driver_sql("DROP TABLE restores")
    bind.exec_driver_sql("ALTER TABLE restores__new RENAME TO restores")
    bind.exec_driver_sql(
        "CREATE INDEX restores_cluster_idx ON restores "
        "(db_host, db_port, database_name, restored_at DESC)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX restores_cluster_identity_idx ON restores "
        "(cluster_id, db_host, db_port, database_name, restored_at DESC)"
    )

    bind.exec_driver_sql(
        """CREATE TABLE database_events__new (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('restored', 'dropped')),
            occurred_at TEXT NOT NULL,
            backup_id TEXT REFERENCES backups(id),
            source_kind TEXT CHECK (source_kind IS NULL OR source_kind IN ('catalogue', 'local_archive')),
            source_sha256 TEXT,
            cluster_id TEXT,
            data_directory TEXT,
            CHECK (
                event_type = 'dropped' OR
                ((source_kind = 'catalogue' AND backup_id IS NOT NULL AND source_sha256 IS NULL)
                 OR (source_kind = 'local_archive' AND backup_id IS NULL AND source_sha256 IS NOT NULL
                     AND length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'))
            )
        )"""
    )
    bind.exec_driver_sql(
        """INSERT INTO database_events__new
           (sequence, db_host, db_port, database_name, event_type, occurred_at, backup_id,
            source_kind, source_sha256, cluster_id, data_directory)
           SELECT sequence, db_host, db_port, database_name, event_type, occurred_at, backup_id,
                  CASE WHEN event_type = 'restored' THEN 'catalogue' ELSE NULL END,
                  NULL, cluster_id, data_directory
           FROM database_events"""
    )
    bind.exec_driver_sql("DROP TABLE database_events")
    bind.exec_driver_sql("ALTER TABLE database_events__new RENAME TO database_events")
    bind.exec_driver_sql(
        "CREATE INDEX database_events_cluster_idx ON database_events "
        "(db_host, db_port, database_name, sequence DESC)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX database_events_cluster_identity_idx ON database_events "
        "(cluster_id, db_host, db_port, database_name, sequence DESC)"
    )


def downgrade() -> None:
    """Refuse a lossy downgrade while local provenance is present."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    local_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM restores WHERE source_kind='local_archive' "
        "UNION ALL SELECT COUNT(*) FROM database_events WHERE source_kind='local_archive'"
    ).fetchall()
    if any(int(row[0]) for row in local_count):
        raise RuntimeError("cannot downgrade while local-archive provenance exists")
    bind.exec_driver_sql(
        """CREATE TABLE database_events__old (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('restored', 'dropped')),
            occurred_at TEXT NOT NULL,
            backup_id TEXT REFERENCES backups(id),
            cluster_id TEXT,
            data_directory TEXT,
            CHECK (event_type = 'dropped' OR backup_id IS NOT NULL)
        )"""
    )
    bind.exec_driver_sql(
        """INSERT INTO database_events__old
           SELECT sequence, db_host, db_port, database_name, event_type, occurred_at, backup_id,
                  cluster_id, data_directory
           FROM database_events"""
    )
    bind.exec_driver_sql("DROP TABLE database_events")
    bind.exec_driver_sql("ALTER TABLE database_events__old RENAME TO database_events")
    bind.exec_driver_sql(
        "CREATE INDEX database_events_cluster_idx ON database_events "
        "(db_host, db_port, database_name, sequence DESC)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX database_events_cluster_identity_idx ON database_events "
        "(cluster_id, db_host, db_port, database_name, sequence DESC)"
    )
    bind.exec_driver_sql(
        """CREATE TABLE restores__old (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            backup_id TEXT NOT NULL REFERENCES backups(id),
            restored_at TEXT NOT NULL,
            cluster_id TEXT,
            data_directory TEXT
        )"""
    )
    bind.exec_driver_sql(
        """INSERT INTO restores__old
           SELECT sequence, db_host, db_port, database_name, backup_id, restored_at,
                  cluster_id, data_directory
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
