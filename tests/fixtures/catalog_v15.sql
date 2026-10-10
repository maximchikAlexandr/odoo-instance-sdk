-- Schema-only fixture captured from a pre-Alembic v15 catalogue.
PRAGMA foreign_keys = ON;

CREATE TABLE backup_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_id TEXT NOT NULL REFERENCES backups(id),
                event_type TEXT NOT NULL CHECK (event_type IN ('download_started', 'download_succeeded', 'download_failed', 'validation_succeeded', 'validation_failed', 'validation_unavailable', 'deleted')),
                occurred_at TEXT NOT NULL,
                path TEXT,
                validator TEXT,
                exit_code INTEGER,
                message TEXT
            );

CREATE TABLE backups (
                id TEXT PRIMARY KEY,
                source_base_url TEXT NOT NULL,
                database_name TEXT NOT NULL,
                format TEXT NOT NULL CHECK (format IN ('zip', 'dump')),
                filestore_requested INTEGER NOT NULL CHECK (filestore_requested IN (0, 1)),
                path TEXT,
                filename TEXT,
                size_bytes INTEGER,
                sha256 TEXT,
                state TEXT NOT NULL CHECK (state IN ('downloading', 'available', 'failed', 'deleted')),
                started_at TEXT NOT NULL,
                downloaded_at TEXT,
                failed_at TEXT,
                deleted_at TEXT,
                error_type TEXT,
                error_message TEXT
            , source_git_branch TEXT);

CREATE TABLE database_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_host TEXT NOT NULL,
                    db_port INTEGER NOT NULL,
                    database_name TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK (event_type IN ('restored', 'dropped')),
                    occurred_at TEXT NOT NULL,
                    backup_id TEXT, cluster_id TEXT, data_directory TEXT,
                    CHECK (event_type = 'dropped' OR backup_id IS NOT NULL),
                    FOREIGN KEY (backup_id) REFERENCES backups(id)
                );

CREATE TABLE "environment_copy_journal" (
                    environment_id TEXT PRIMARY KEY REFERENCES environments(id),
                    target_database TEXT NOT NULL,
                    db_host TEXT NOT NULL,
                    db_port INTEGER NOT NULL,
                    db_user TEXT,
                    backup_id TEXT REFERENCES backups(id),
                    stage TEXT NOT NULL CHECK (stage IN ('prepared', 'backed_up', 'restore_pending', 'restored', 'dropped', 'backup_deleted')),
                    updated_at TEXT NOT NULL
                );

CREATE TABLE "environment_events" (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    environment_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK (operation IN ('checkout', 'sync', 'use', 'shell', 'remove')),
                    outcome TEXT NOT NULL CHECK (outcome IN ('started', 'succeeded', 'failed')),
                    occurred_at TEXT NOT NULL,
                    message TEXT,
                    FOREIGN KEY (environment_id) REFERENCES environments(id)
                );

CREATE TABLE environments (
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
                    backup_id TEXT,
                    runtime_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    removed_at TEXT,
                    last_error TEXT, applied_settings_json TEXT NOT NULL DEFAULT '{"components":{"addons":{"status":"unknown"},"dependencies":{"status":"unknown"},"git":{"status":"unknown"},"odoo":{"status":"unknown"},"python":{"status":"unknown"}},"version":1}',
                    FOREIGN KEY (backup_id) REFERENCES backups(id)
                );

CREATE TABLE postgres_clusters (
                    cluster_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    compose_project TEXT NOT NULL,
                    volume_name TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'active')),
                    created_at TEXT NOT NULL,
                    activated_at TEXT
                );

CREATE TABLE projects (
                    project_id TEXT PRIMARY KEY,
                    repository_root TEXT NOT NULL,
                    git_common_dir TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

CREATE TABLE restores (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_host TEXT NOT NULL,
                    db_port INTEGER NOT NULL,
                    database_name TEXT NOT NULL,
                    backup_id TEXT NOT NULL REFERENCES backups(id),
                    restored_at TEXT NOT NULL
                , cluster_id TEXT, data_directory TEXT);

CREATE TABLE runtime (
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
                    PRIMARY KEY (owner_kind, owner_id)
                );

CREATE INDEX backup_events_backup_idx ON backup_events (backup_id, sequence DESC);

CREATE INDEX backups_lookup_idx ON backups (source_base_url, database_name, downloaded_at DESC);

CREATE INDEX backups_point_order_idx ON backups (COALESCE(downloaded_at, started_at) DESC, id ASC);

CREATE INDEX backups_state_idx ON backups (state);

CREATE INDEX database_events_cluster_idx ON database_events (db_host, db_port, database_name, sequence DESC);

CREATE INDEX environment_events_env_idx
                    ON environment_events (environment_id, sequence DESC);

CREATE INDEX environments_active_idx
                    ON environments (git_common_dir, branch, state);

CREATE UNIQUE INDEX environments_one_active_branch
                    ON environments(git_common_dir, branch) WHERE state <> 'removed';

CREATE UNIQUE INDEX postgres_clusters_project_idx ON postgres_clusters(project_id);

CREATE UNIQUE INDEX projects_identity_idx ON projects(repository_root, git_common_dir);

CREATE INDEX restores_cluster_idx ON restores (db_host, db_port, database_name, restored_at DESC);

CREATE VIEW environment_runtime AS
                   SELECT owner_id AS environment_id, root_pid, create_time, started_at,
                          checkout_branch, commit_sha, http_url, http_port, database_name, updated_at
                   FROM runtime WHERE owner_kind = 'environment';

PRAGMA user_version = 15;
