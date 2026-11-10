"""Record proven post-import restore progress."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection) -> bool:
    return any(
        row[1] == "post_restore_checkpoint"
        for row in bind.exec_driver_sql("PRAGMA table_info(restores)")
    )


def upgrade() -> None:
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind):
        bind.exec_driver_sql(
            "ALTER TABLE restores ADD COLUMN post_restore_checkpoint TEXT "
            "CHECK (post_restore_checkpoint IN "
            "('reset_pending', 'reset_complete', 'reset_not_requested'))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if _has_column(bind):
        bind.exec_driver_sql("ALTER TABLE restores DROP COLUMN post_restore_checkpoint")
