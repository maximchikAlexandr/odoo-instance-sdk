"""Persist PostgreSQL OID evidence for project database bindings."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection, column: str) -> bool:
    return any(
        row[1] == column
        for row in bind.exec_driver_sql("PRAGMA table_info(project_database_bindings)")
    )


def upgrade() -> None:
    """Add nullable identity evidence; pre-existing rows remain unproven."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind, "database_oid"):
        bind.exec_driver_sql(
            "ALTER TABLE project_database_bindings ADD COLUMN database_oid INTEGER"
        )


def downgrade() -> None:
    """Refuse to erase durable binding evidence."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if int(bind.exec_driver_sql("SELECT COUNT(*) FROM project_database_bindings").scalar_one()):
        raise RuntimeError("cannot downgrade while project database bindings exist")
