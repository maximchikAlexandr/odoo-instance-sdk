"""Persist the secret-free identity captured for managed runtime launches."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

from odoo_instance_sdk.storage.catalog_schema import ENVIRONMENT_RUNTIME_VIEW_SQL

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_ENVIRONMENT_RUNTIME_VIEW_SQL = """
CREATE VIEW environment_runtime AS
    SELECT owner_id AS environment_id, root_pid, create_time, started_at,
           checkout_branch, commit_sha, http_url, http_port, database_name, updated_at
    FROM runtime WHERE owner_kind = 'environment'
"""


def _has_column(bind: Connection) -> bool:
    return any(
        row[1] == "launch_identity_json"
        for row in bind.exec_driver_sql("PRAGMA table_info(runtime)")
    )


def upgrade() -> None:
    """Add the nullable launch identity without fabricating legacy evidence."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if _has_column(bind):
        return
    bind.exec_driver_sql("DROP VIEW IF EXISTS environment_runtime")
    bind.exec_driver_sql("ALTER TABLE runtime ADD COLUMN launch_identity_json TEXT")
    bind.exec_driver_sql(ENVIRONMENT_RUNTIME_VIEW_SQL)


def downgrade() -> None:
    """Remove the nullable identity column while preserving runtime rows."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind):
        return
    bind.exec_driver_sql("DROP VIEW IF EXISTS environment_runtime")
    bind.exec_driver_sql(
        """CREATE TABLE runtime__old (
            owner_kind TEXT NOT NULL CHECK (owner_kind IN ('environment', 'project')),
            owner_id TEXT NOT NULL CHECK (length(trim(owner_id)) > 0),
            root_pid INTEGER NOT NULL,
            create_time REAL NOT NULL,
            started_at TEXT NOT NULL,
            checkout_branch TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            http_url TEXT NOT NULL,
            http_port INTEGER NOT NULL,
            database_name TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(owner_kind, owner_id)
        )"""
    )
    bind.exec_driver_sql(
        """INSERT INTO runtime__old
           (owner_kind, owner_id, root_pid, create_time, started_at, checkout_branch,
            commit_sha, http_url, http_port, database_name, updated_at)
           SELECT owner_kind, owner_id, root_pid, create_time, started_at, checkout_branch,
                  commit_sha, http_url, http_port, database_name, updated_at
           FROM runtime"""
    )
    bind.exec_driver_sql("DROP TABLE runtime")
    bind.exec_driver_sql("ALTER TABLE runtime__old RENAME TO runtime")
    bind.exec_driver_sql(_LEGACY_ENVIRONMENT_RUNTIME_VIEW_SQL)
