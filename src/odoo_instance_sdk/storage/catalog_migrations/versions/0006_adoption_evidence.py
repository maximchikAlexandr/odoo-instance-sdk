"""Persist project, checkout, ownership, and artifact evidence."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add additive adoption evidence without changing existing lifecycle rows."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    columns = {
        str(row[1]) for row in bind.exec_driver_sql("PRAGMA table_info(environments)").fetchall()
    }
    if "artifact_root" in columns:
        return

    bind.exec_driver_sql(
        """CREATE TABLE environments__new (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            repository_root TEXT NOT NULL,
            git_common_dir TEXT NOT NULL,
            branch TEXT NOT NULL,
            base_ref TEXT NOT NULL,
            worktree_path TEXT NOT NULL,
            generated_config_path TEXT NOT NULL,
            python_environment_path TEXT NOT NULL,
            python_environment_owned INTEGER NOT NULL,
            dependency_lock_path TEXT NOT NULL,
            db_mode TEXT NOT NULL,
            source_db_name TEXT,
            target_db_name TEXT,
            backup_id TEXT REFERENCES backups(id),
            runtime_json TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            removed_at TEXT,
            last_error TEXT,
            applied_settings_json TEXT NOT NULL DEFAULT '{"status":"unknown"}',
            project_id TEXT REFERENCES projects(project_id),
            checkout_repository_root TEXT,
            checkout_git_common_dir TEXT,
            checkout_commit_sha TEXT,
            code_ownership TEXT NOT NULL DEFAULT 'unknown'
                CHECK (code_ownership IN ('sdk_owned', 'caller_owned', 'unknown')),
            artifact_root TEXT,
            adoption_input_fingerprint TEXT
        )"""
    )
    bind.exec_driver_sql(
        """INSERT INTO environments__new (
            id, name, repository_root, git_common_dir, branch, base_ref,
            worktree_path, generated_config_path, python_environment_path,
            python_environment_owned, dependency_lock_path, db_mode,
            source_db_name, target_db_name, backup_id, runtime_json, state,
            created_at, last_used_at, removed_at, last_error, applied_settings_json,
            project_id, checkout_repository_root, checkout_git_common_dir,
            checkout_commit_sha, code_ownership, artifact_root
        )
        SELECT e.id, e.name, e.repository_root, e.git_common_dir, e.branch, e.base_ref,
               e.worktree_path, e.generated_config_path, e.python_environment_path,
               e.python_environment_owned, e.dependency_lock_path, e.db_mode,
               e.source_db_name, e.target_db_name, e.backup_id, e.runtime_json, e.state,
               e.created_at, e.last_used_at, e.removed_at, e.last_error,
               e.applied_settings_json,
               (SELECT MIN(p.project_id) FROM projects p
                WHERE p.repository_root = e.repository_root
                  AND p.git_common_dir = e.git_common_dir),
               e.repository_root, e.git_common_dir, NULL,
               CASE
                 WHEN e.worktree_path LIKE '%/.odcli/environments/%/worktree'
                 THEN 'sdk_owned' ELSE 'unknown' END,
               substr(e.worktree_path, 1, length(e.worktree_path) - length('/worktree'))
        FROM environments e"""
    )
    bind.exec_driver_sql("DROP TABLE environments")
    bind.exec_driver_sql("ALTER TABLE environments__new RENAME TO environments")
    bind.exec_driver_sql(
        "CREATE INDEX environments_active_idx ON environments (git_common_dir, branch, state)"
    )
    bind.exec_driver_sql(
        "CREATE UNIQUE INDEX environments_one_active_branch "
        "ON environments(git_common_dir, branch) WHERE state <> 'removed'"
    )
    bind.exec_driver_sql(
        "CREATE INDEX environments_project_checkout_idx "
        "ON environments(project_id, checkout_git_common_dir, worktree_path)"
    )


def downgrade() -> None:
    """Adoption evidence is forward-only once a caller-owned row exists."""
    bind = op.get_bind()
    assert isinstance(bind, Connection)
    if int(bind.exec_driver_sql("SELECT COUNT(*) FROM environments").scalar_one()):
        raise RuntimeError("catalog adoption evidence migration is forward-only")
    # Historical migration tests downgrade an empty catalogue to exercise an
    # older runtime revision.  Keeping the empty additive table shape is safe;
    # the next upgrade is idempotent and preserves the evidence contract.
