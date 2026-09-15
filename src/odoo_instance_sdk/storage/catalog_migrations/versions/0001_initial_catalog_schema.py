"""Initial catalogue schema."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

from odoo_instance_sdk.storage.catalog_schema import ENVIRONMENT_RUNTIME_VIEW_SQL, metadata

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the complete current catalogue schema in one step."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    metadata.create_all(bind)
    bind.exec_driver_sql(ENVIRONMENT_RUNTIME_VIEW_SQL)


def downgrade() -> None:
    """Drop the catalogue schema."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    bind.exec_driver_sql("DROP VIEW IF EXISTS environment_runtime")
    metadata.drop_all(bind)
