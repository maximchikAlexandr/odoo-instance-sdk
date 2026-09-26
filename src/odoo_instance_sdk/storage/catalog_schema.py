"""SQLAlchemy Core metadata for the backup catalogue schema."""

from __future__ import annotations

from sqlalchemy import (
    REAL,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)

from odoo_instance_sdk.internal.applied_settings import LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON

metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("project_id", Text, primary_key=True),
    Column("repository_root", Text, nullable=False),
    Column("git_common_dir", Text, nullable=False),
    Column("registered_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Index("projects_identity_idx", "repository_root", "git_common_dir", unique=True),
)

backups = Table(
    "backups",
    metadata,
    Column("id", Text, primary_key=True),
    Column("source_base_url", Text, nullable=False),
    Column("database_name", Text, nullable=False),
    Column(
        "format",
        Text,
        CheckConstraint("format IN ('zip', 'dump')"),
        nullable=False,
    ),
    Column(
        "filestore_requested",
        Integer,
        CheckConstraint("filestore_requested IN (0, 1)"),
        nullable=False,
    ),
    Column("path", Text),
    Column("filename", Text),
    Column("size_bytes", Integer),
    Column("sha256", Text),
    Column(
        "state",
        Text,
        CheckConstraint("state IN ('downloading', 'available', 'failed', 'deleted')"),
        nullable=False,
    ),
    Column("started_at", Text, nullable=False),
    Column("downloaded_at", Text),
    Column("failed_at", Text),
    Column("deleted_at", Text),
    Column("error_type", Text),
    Column("error_message", Text),
    Column("project_id", Text, ForeignKey("projects.project_id")),
    Column("source_git_branch", Text),
    Index("backups_lookup_idx", "source_base_url", "database_name", text("downloaded_at DESC")),
    Index("backups_state_idx", "state"),
    Index(
        "backups_point_order_idx",
        text("COALESCE(downloaded_at, started_at) DESC"),
        text("id ASC"),
    ),
    Index("backups_project_idx", "project_id"),
)

backup_events = Table(
    "backup_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("backup_id", Text, ForeignKey("backups.id"), nullable=False),
    Column(
        "event_type",
        Text,
        CheckConstraint(
            "event_type IN ('download_started', 'download_succeeded', 'download_failed', "
            "'validation_succeeded', 'validation_failed', 'validation_unavailable', 'deleted')"
        ),
        nullable=False,
    ),
    Column("occurred_at", Text, nullable=False),
    Column("path", Text),
    Column("validator", Text),
    Column("exit_code", Integer),
    Column("message", Text),
    Index("backup_events_backup_idx", "backup_id", text("sequence DESC")),
)

restores = Table(
    "restores",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("db_host", Text, nullable=False),
    Column("db_port", Integer, nullable=False),
    Column("database_name", Text, nullable=False),
    Column("backup_id", Text, ForeignKey("backups.id")),
    Column(
        "source_kind",
        Text,
        CheckConstraint("source_kind IN ('catalogue', 'local_archive')"),
        nullable=False,
    ),
    Column("source_sha256", Text),
    Column("restored_at", Text, nullable=False),
    Column("cluster_id", Text),
    Column("data_directory", Text),
    CheckConstraint(
        "(source_kind = 'catalogue' AND backup_id IS NOT NULL AND source_sha256 IS NULL) "
        "OR (source_kind = 'local_archive' AND backup_id IS NULL AND source_sha256 IS NOT NULL "
        "AND length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*')"
    ),
    Index(
        "restores_cluster_idx",
        "db_host",
        "db_port",
        "database_name",
        text("restored_at DESC"),
    ),
    Index(
        "restores_cluster_identity_idx",
        "cluster_id",
        "db_host",
        "db_port",
        "database_name",
        text("restored_at DESC"),
    ),
)

database_events = Table(
    "database_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("db_host", Text, nullable=False),
    Column("db_port", Integer, nullable=False),
    Column("database_name", Text, nullable=False),
    Column(
        "event_type",
        Text,
        CheckConstraint("event_type IN ('restored', 'dropped')"),
        nullable=False,
    ),
    Column("occurred_at", Text, nullable=False),
    Column("backup_id", Text, ForeignKey("backups.id")),
    Column(
        "source_kind",
        Text,
        CheckConstraint("source_kind IN ('catalogue', 'local_archive')"),
    ),
    Column("source_sha256", Text),
    Column("cluster_id", Text),
    Column("data_directory", Text),
    CheckConstraint(
        "event_type = 'dropped' OR "
        "((source_kind = 'catalogue' AND backup_id IS NOT NULL AND source_sha256 IS NULL) "
        "OR (source_kind = 'local_archive' AND backup_id IS NULL AND source_sha256 IS NOT NULL "
        "AND length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'))"
    ),
    Index(
        "database_events_cluster_idx",
        "db_host",
        "db_port",
        "database_name",
        text("sequence DESC"),
    ),
    Index(
        "database_events_cluster_identity_idx",
        "cluster_id",
        "db_host",
        "db_port",
        "database_name",
        text("sequence DESC"),
    ),
)

environments = Table(
    "environments",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("repository_root", Text, nullable=False),
    Column("git_common_dir", Text, nullable=False),
    Column("branch", Text, nullable=False),
    Column("base_ref", Text, nullable=False),
    Column("worktree_path", Text, nullable=False),
    Column("generated_config_path", Text, nullable=False),
    Column("python_environment_path", Text, nullable=False),
    Column("python_environment_owned", Integer, nullable=False),
    Column("dependency_lock_path", Text, nullable=False),
    Column("db_mode", Text, nullable=False),
    Column("source_db_name", Text),
    Column("target_db_name", Text),
    Column("backup_id", Text, ForeignKey("backups.id")),
    Column("runtime_json", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("last_used_at", Text),
    Column("removed_at", Text),
    Column("last_error", Text),
    Column(
        "applied_settings_json",
        Text,
        nullable=False,
        server_default=LEGACY_UNKNOWN_APPLIED_SETTINGS_JSON,
    ),
    Index("environments_active_idx", "git_common_dir", "branch", "state"),
    Index(
        "environments_one_active_branch",
        "git_common_dir",
        "branch",
        unique=True,
        sqlite_where=text("state <> 'removed'"),
    ),
)

environment_events = Table(
    "environment_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("environment_id", Text, ForeignKey("environments.id"), nullable=False),
    Column(
        "operation",
        Text,
        CheckConstraint("operation IN ('checkout', 'sync', 'use', 'shell', 'remove')"),
        nullable=False,
    ),
    Column(
        "outcome",
        Text,
        CheckConstraint("outcome IN ('started', 'succeeded', 'failed')"),
        nullable=False,
    ),
    Column("occurred_at", Text, nullable=False),
    Column("message", Text),
    Index("environment_events_env_idx", "environment_id", text("sequence DESC")),
)

environment_copy_journal = Table(
    "environment_copy_journal",
    metadata,
    Column("environment_id", Text, ForeignKey("environments.id"), primary_key=True),
    Column("target_database", Text, nullable=False),
    Column("db_host", Text, nullable=False),
    Column("db_port", Integer, nullable=False),
    Column("db_user", Text),
    Column("backup_id", Text, ForeignKey("backups.id")),
    Column(
        "stage",
        Text,
        CheckConstraint(
            "stage IN ('prepared', 'backed_up', 'restore_pending', 'restored', "
            "'dropped', 'backup_deleted')"
        ),
        nullable=False,
    ),
    Column("updated_at", Text, nullable=False),
)

postgres_clusters = Table(
    "postgres_clusters",
    metadata,
    Column("cluster_id", Text, primary_key=True),
    Column("project_id", Text, nullable=False),
    Column("compose_project", Text, nullable=False),
    Column("volume_name", Text, nullable=False),
    Column(
        "state",
        Text,
        CheckConstraint("state IN ('pending', 'active')"),
        nullable=False,
    ),
    Column("created_at", Text, nullable=False),
    Column("activated_at", Text),
    Index("postgres_clusters_project_idx", "project_id", unique=True),
)

runtime = Table(
    "runtime",
    metadata,
    Column(
        "owner_kind",
        Text,
        CheckConstraint("owner_kind IN ('environment', 'project')"),
        nullable=False,
    ),
    Column(
        "owner_id",
        Text,
        CheckConstraint("length(trim(owner_id)) > 0"),
        nullable=False,
    ),
    Column("root_pid", Integer, nullable=False),
    Column("create_time", REAL, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("checkout_branch", Text, nullable=False),
    Column("commit_sha", Text, nullable=False),
    Column("http_url", Text, nullable=False),
    Column("http_port", Integer, nullable=False),
    Column("database_name", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    UniqueConstraint("owner_kind", "owner_id"),
)

CATALOG_TABLES = (
    "projects",
    "backups",
    "backup_events",
    "restores",
    "database_events",
    "environments",
    "environment_events",
    "environment_copy_journal",
    "postgres_clusters",
    "runtime",
)

CATALOG_INDEXES = (
    "projects_identity_idx",
    "backups_lookup_idx",
    "backups_state_idx",
    "backups_point_order_idx",
    "backups_project_idx",
    "backup_events_backup_idx",
    "restores_cluster_idx",
    "restores_cluster_identity_idx",
    "database_events_cluster_idx",
    "database_events_cluster_identity_idx",
    "environments_active_idx",
    "environments_one_active_branch",
    "environment_events_env_idx",
    "postgres_clusters_project_idx",
)

ENVIRONMENT_RUNTIME_VIEW_SQL = """
CREATE VIEW environment_runtime AS
    SELECT owner_id AS environment_id, root_pid, create_time, started_at,
           checkout_branch, commit_sha, http_url, http_port, database_name, updated_at
    FROM runtime WHERE owner_kind = 'environment'
"""
