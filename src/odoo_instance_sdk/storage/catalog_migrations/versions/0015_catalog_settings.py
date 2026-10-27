"""Add revisioned public catalogue settings."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    bind.exec_driver_sql(
        """CREATE TABLE settings (
            \"key\" TEXT NOT NULL,
            value_json TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (\"key\"),
            CHECK (length(trim(key)) > 0),
            CHECK (json_valid(value_json))
        )"""
    )


def downgrade() -> None:
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    bind.exec_driver_sql("DROP TABLE settings")
