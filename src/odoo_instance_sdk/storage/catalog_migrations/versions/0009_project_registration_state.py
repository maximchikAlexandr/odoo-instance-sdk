"""Track whether a project is visible in ordinary catalogue reads."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(bind: Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in bind.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade() -> None:
    """Backfill every existing project as registered."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if _has_column(bind, "projects", "registration_state"):
        return
    bind.exec_driver_sql(
        "ALTER TABLE projects ADD COLUMN registration_state TEXT NOT NULL DEFAULT 'registered' "
        "CHECK (registration_state IN ('registered', 'forgotten'))"
    )


def downgrade() -> None:
    """Refuse to erase forgotten state, otherwise restore the prior table shape."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if not _has_column(bind, "projects", "registration_state"):
        return
    if int(
        bind.exec_driver_sql(
            "SELECT COUNT(*) FROM projects WHERE registration_state = 'forgotten'"
        ).scalar_one()
    ):
        raise RuntimeError("cannot downgrade project registration state with forgotten rows")

    bind.exec_driver_sql(
        """CREATE TABLE projects__old (
            project_id TEXT PRIMARY KEY,
            repository_root TEXT NOT NULL,
            git_common_dir TEXT NOT NULL,
            registered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    bind.exec_driver_sql(
        """INSERT INTO projects__old
           (project_id, repository_root, git_common_dir, registered_at, updated_at)
           SELECT project_id, repository_root, git_common_dir, registered_at, updated_at
           FROM projects"""
    )
    bind.exec_driver_sql("DROP TABLE projects")
    bind.exec_driver_sql("ALTER TABLE projects__old RENAME TO projects")
    bind.exec_driver_sql(
        "CREATE UNIQUE INDEX projects_identity_idx ON projects(repository_root, git_common_dir)"
    )
