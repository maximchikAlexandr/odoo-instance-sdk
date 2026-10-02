"""Record exact ownership for databases created by Compose bootstrap."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rebuild only the lifecycle table so its CHECK admits bootstrap events."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    table_sql = str(
        bind.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='database_events'"
        ).scalar_one()
    )
    if "'bootstrapped'" in table_sql:
        return
    bind.exec_driver_sql(
        """CREATE TABLE database_events__new (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('restored', 'bootstrapped', 'dropped')),
            occurred_at TEXT NOT NULL,
            backup_id TEXT REFERENCES backups(id),
            source_kind TEXT CHECK (source_kind IS NULL OR source_kind IN ('catalogue', 'local_archive')),
            source_sha256 TEXT,
            cluster_id TEXT,
            data_directory TEXT,
            CHECK (
                event_type = 'dropped' OR
                (event_type = 'bootstrapped' AND database_name = 'tmp' AND backup_id IS NULL
                 AND source_kind IS NULL AND source_sha256 IS NULL AND cluster_id IS NOT NULL
                 AND data_directory IS NOT NULL AND length(trim(data_directory)) > 0) OR
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
                  source_kind, source_sha256, cluster_id, data_directory
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
    """Refuse to discard bootstrap ownership history."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if int(
        bind.exec_driver_sql(
            "SELECT COUNT(*) FROM database_events WHERE event_type='bootstrapped'"
        ).scalar_one()
    ):
        raise RuntimeError("cannot downgrade while bootstrap provenance exists")
    bind.exec_driver_sql(
        """CREATE TABLE database_events__old (
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
        """INSERT INTO database_events__old
           SELECT sequence, db_host, db_port, database_name, event_type, occurred_at, backup_id,
                  source_kind, source_sha256, cluster_id, data_directory
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
