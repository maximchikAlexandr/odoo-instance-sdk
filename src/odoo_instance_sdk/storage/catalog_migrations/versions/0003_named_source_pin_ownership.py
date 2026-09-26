"""Add historical source, pin state and COPY journal ownership."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in bind.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade() -> None:
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind, "backups", "source_name"):
        bind.exec_driver_sql("ALTER TABLE backups ADD COLUMN source_name TEXT")
    if not _has_column(bind, "backups", "pinned"):
        bind.exec_driver_sql(
            "ALTER TABLE backups ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0 "
            "CHECK (pinned IN (0, 1))"
        )
    if not _has_column(bind, "environment_copy_journal", "backup_ownership"):
        bind.exec_driver_sql(
            "ALTER TABLE environment_copy_journal ADD COLUMN backup_ownership TEXT "
            "DEFAULT 'unknown' CHECK (backup_ownership IS NULL OR "
            "backup_ownership IN ('owned', 'borrowed', 'unknown'))"
        )

    # SQLite cannot alter a table-level CHECK constraint. Rebuild the tiny
    # append-only event table while preserving its sequence and audit rows.
    event_check = bind.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='backup_events'"
    ).scalar_one()
    if "'pin_set'" not in str(event_check):
        bind.exec_driver_sql(
            """CREATE TABLE backup_events__new (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_id TEXT NOT NULL REFERENCES backups(id),
                event_type TEXT NOT NULL CHECK (event_type IN (
                    'download_started', 'download_succeeded', 'download_failed',
                    'validation_succeeded', 'validation_failed', 'validation_unavailable',
                    'pin_set', 'deleted'
                )),
                occurred_at TEXT NOT NULL,
                path TEXT,
                validator TEXT,
                exit_code INTEGER,
                message TEXT
            )"""
        )
        bind.exec_driver_sql(
            """INSERT INTO backup_events__new
               (sequence, backup_id, event_type, occurred_at, path, validator, exit_code, message)
               SELECT sequence, backup_id, event_type, occurred_at, path, validator, exit_code, message
               FROM backup_events"""
        )
        bind.exec_driver_sql("DROP TABLE backup_events")
        bind.exec_driver_sql("ALTER TABLE backup_events__new RENAME TO backup_events")
        bind.exec_driver_sql(
            "CREATE INDEX backup_events_backup_idx ON backup_events(backup_id, sequence DESC)"
        )

    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS backups_source_group_idx ON backups "
        "(project_id, source_name, downloaded_at DESC)"
    )


def downgrade() -> None:
    raise RuntimeError("catalog revision 0003 is forward-only")
