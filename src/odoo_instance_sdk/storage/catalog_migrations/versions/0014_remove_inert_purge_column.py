"""Remove the inert legacy purge marker from the catalogue schema."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection) -> bool:
    return any(
        row[1] == "purge_completed_at"
        for row in bind.exec_driver_sql("PRAGMA table_info(projects)")
    )


def upgrade() -> None:
    """Reconcile revision 0013 without keeping completion-state authority."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind):
        raise RuntimeError("revision 0013 catalogue is missing its legacy projects column")
    bind.exec_driver_sql("ALTER TABLE projects DROP COLUMN purge_completed_at")


def downgrade() -> None:
    """Restore only the nullable compatibility column for revision 0013."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind):
        bind.exec_driver_sql("ALTER TABLE projects ADD COLUMN purge_completed_at TEXT")
