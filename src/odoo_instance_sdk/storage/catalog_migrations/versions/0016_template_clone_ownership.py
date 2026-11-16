"""Record COPY strategy and the exact template filestore path."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(bind: Connection) -> set[str]:
    return {str(row[1]) for row in bind.exec_driver_sql("PRAGMA table_info(environments)")}


def upgrade() -> None:
    """Backfill existing COPY rows as backup clones and keep template paths empty."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    columns = _columns(bind)
    if "db_copy_strategy" not in columns:
        bind.exec_driver_sql("ALTER TABLE environments ADD COLUMN db_copy_strategy TEXT")
    if "target_filestore_path" not in columns:
        bind.exec_driver_sql("ALTER TABLE environments ADD COLUMN target_filestore_path TEXT")
    bind.exec_driver_sql(
        "UPDATE environments SET db_copy_strategy = 'backup' "
        "WHERE db_mode = 'copy' AND db_copy_strategy IS NULL"
    )


def downgrade() -> None:
    """Return to revision 0015 when no template environment owns a filestore path."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    template_rows = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM environments WHERE db_copy_strategy = 'template'"
    ).scalar_one()
    if int(template_rows or 0) > 0:
        raise RuntimeError("cannot downgrade while a template environment exists")
    columns = _columns(bind)
    if "target_filestore_path" in columns:
        bind.exec_driver_sql("ALTER TABLE environments DROP COLUMN target_filestore_path")
    if "db_copy_strategy" in columns:
        bind.exec_driver_sql("ALTER TABLE environments DROP COLUMN db_copy_strategy")
