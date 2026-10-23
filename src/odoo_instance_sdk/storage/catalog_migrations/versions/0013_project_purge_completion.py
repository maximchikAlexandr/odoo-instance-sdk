"""Record fully completed project purges for inert full-id retries."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mark only purges whose captured actions all completed."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not any(
        row[1] == "purge_completed_at"
        for row in bind.exec_driver_sql("PRAGMA table_info(projects)")
    ):
        bind.exec_driver_sql("ALTER TABLE projects ADD COLUMN purge_completed_at TEXT")


def downgrade() -> None:
    """Retain the nullable completion marker on downgrade."""
