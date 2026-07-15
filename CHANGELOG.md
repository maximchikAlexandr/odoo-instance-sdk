# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-15

### Added
- `instance.databases` — database operations bound to a specific Odoo instance
- `client.instance(base_url, master_password)` — create an instance by URL
- `client.instance.from_config(path)` — create an instance from `odoo.conf`
- `client.backups` — catalog resource (list, latest, history, validate, delete)
- Persistent backup catalog via SQLite (WAL mode) under `~/.cache/odoo-instance-sdk/`
- ZIP validation (`is_zipfile`, manifest, CRC) and optional `pg_restore` validation
- Audited download with UUID tracking and `.part` files for atomic completion
- `BackupEvent`, `BackupState`, `BackupFormat`, `BackupValidationStatus` enums
- `Database` and `NoBackup` models — `database.backup` provides restore-mapping per database
- `databases.current()` — returns `Database` for the first configured database name
- `databases[n]` — positional indexing from `list()` via `__getitem__`
- Restore-tracking via SQLite `restores` and `database_events` tables (schema v2)
- InstanceConfig fields `db_host`, `db_port`, `db_user`, `db_password` (cluster-key)
- Lazy reconciliation: `list()`/`exists()` detect dropped databases and record events
- Fallback `psql` verification when Odoo database manager is unreachable
- Server lifecycle management (start, stop, status, run, wait_ready)
- Readiness checks via HTTP health endpoint (`/web/health`)
- Local-only guard for destructive operations (restore, drop)

### Removed (breaking)
- `client.database` — use `instance.databases`
- `client.server` — use `instance.start()` and lifecycle methods on `OdooInstance`
- `BackupArtifact` — replaced by `Backup`
- `RemoteInstanceError` — renamed to `NonLocalInstanceError`
- HTTP Basic Auth — `master_pwd` sent only as form field

### Changed (breaking)
- `databases.list()` returns `tuple[Database, ...]` instead of `tuple[str, ...]`
- Catalog schema v2 — `PRAGMA user_version` migration from v0
- `OdooClientConfig` carries only `executable`, `backups_directory` (optional) and `http_timeout_seconds`; each `OdooInstance` provides its own `base_url` and master password
- Process registry shared across instances on `OdooClient`
- `warn_if_cleartext_auth` → `warn_if_cleartext_secret`

### Security
- `master_pwd` and `db_password` are never in `repr`, exception messages, or logs
- Destructive operations (`restore`, `drop`) guarded by `NonLocalInstanceError`
- `MasterPasswordRequiredError` raised early when password is missing
- Backup filenames start with the backup UUID; paths contained within the destination directory
- HTTP interface defaults to loopback only
- Cleartext warning fires once per process when master password is sent over HTTP to non-local hosts
- Path-traversal protection in backup() — database name validated
- `master_pwd` redacted from `DatabaseError.body`
- Catalog file and WAL/SHM sidecars are `chmod 0600`