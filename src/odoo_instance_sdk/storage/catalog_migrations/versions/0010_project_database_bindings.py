"""Persist project-scoped database ownership captured during init."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the durable project/claim/database association table."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    bind.exec_driver_sql(
        """CREATE TABLE IF NOT EXISTS project_database_bindings (
            project_id TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            db_host TEXT NOT NULL,
            db_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            bound_at TEXT NOT NULL,
            UNIQUE(project_id, cluster_id, db_host, db_port, database_name)
        )"""
    )
    bind.exec_driver_sql(
        """CREATE INDEX IF NOT EXISTS project_database_bindings_identity_idx
           ON project_database_bindings
              (project_id, cluster_id, db_host, db_port, database_name)"""
    )


def downgrade() -> None:
    """Never silently erase durable ownership evidence."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if int(bind.exec_driver_sql("SELECT COUNT(*) FROM project_database_bindings").scalar_one()):
        raise RuntimeError("cannot downgrade while project database bindings exist")
    bind.exec_driver_sql("DROP INDEX IF EXISTS project_database_bindings_identity_idx")
    bind.exec_driver_sql("DROP TABLE IF EXISTS project_database_bindings")
