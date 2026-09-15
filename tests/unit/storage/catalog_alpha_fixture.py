"""Build a known-alpha catalogue at the current schema without Alembic stamping."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from odoo_instance_sdk.storage.backup_catalog import BackupCatalog


def write_alpha_catalog(db_path: Path) -> tuple[str, str, str]:
    """Create a stamped catalogue, drop Alembic metadata, and return fixture ids."""
    catalog = BackupCatalog(db_path=db_path)
    backup_id = str(uuid.uuid4())
    environment_id = str(uuid.uuid4())
    project_id = "project_alpha-fixture"
    catalog._conn.execute(
        """INSERT INTO projects
           (project_id, repository_root, git_common_dir, registered_at, updated_at)
           VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
        (project_id, "/repo", "/repo/.git"),
    )
    catalog._conn.execute(
        """INSERT INTO backups
           (id, source_base_url, database_name, format, filestore_requested, state, started_at,
            project_id, source_git_branch)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)""",
        (backup_id, "https://example.test", "alpha", "zip", 1, "available", project_id, "main"),
    )
    catalog._conn.execute(
        """INSERT INTO backup_events (backup_id, event_type, occurred_at)
           VALUES (?, ?, datetime('now'))""",
        (backup_id, "download_succeeded"),
    )
    catalog._conn.execute(
        """INSERT INTO environments
           (id, name, repository_root, git_common_dir, branch, base_ref, worktree_path,
            generated_config_path, python_environment_path, python_environment_owned,
            dependency_lock_path, db_mode, source_db_name, target_db_name, backup_id,
            runtime_json, state, created_at, applied_settings_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
        (
            environment_id,
            "alpha",
            "/repo",
            "/repo/.git",
            "main",
            "HEAD",
            "/wt",
            "/wt/odoo.conf",
            "/venv",
            0,
            "/lock",
            "shared",
            "alpha",
            None,
            backup_id,
            "{}",
            "ready",
            '{"components":{"addons":{"status":"unknown"},"dependencies":{"status":"unknown"},"git":{"status":"unknown"},"odoo":{"status":"unknown"},"python":{"status":"unknown"}},"version":1}',
        ),
    )
    catalog._conn.execute(
        """INSERT INTO environment_events (environment_id, operation, outcome, occurred_at)
           VALUES (?, ?, ?, datetime('now'))""",
        (environment_id, "checkout", "succeeded"),
    )
    catalog._conn.commit()
    catalog.close()

    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE alembic_version")
    conn.commit()
    conn.close()
    return backup_id, environment_id, project_id
