"""Add structured COPY replacement recovery evidence."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in bind.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade() -> None:
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind, "environments", "recovery_json"):
        bind.exec_driver_sql("ALTER TABLE environments ADD COLUMN recovery_json TEXT")


def downgrade() -> None:
    # Recovery evidence is intentionally forward-compatible.  Do not rebuild
    # the table and risk erasing a durable failure record during downgrade.
    return
