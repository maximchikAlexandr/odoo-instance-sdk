"""Persist PostgreSQL identity captured after a successful restore."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection, column: str) -> bool:
    return any(row[1] == column for row in bind.exec_driver_sql("PRAGMA table_info(restores)"))


def upgrade() -> None:
    """Add nullable restore identity; legacy restores remain unproven."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind, "database_oid"):
        bind.exec_driver_sql("ALTER TABLE restores ADD COLUMN database_oid INTEGER")


def downgrade() -> None:
    """Keep the nullable column because SQLite cannot safely drop it in place."""
