# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [19.0.0b1] - 2026-07-14

### Added
- `instance.databases` — database operations bound to a specific Odoo instance
- `client.instance(base_url, master_password)` — create an instance by URL
- `client.instance.from_config(path)` — create an instance from `odoo.conf`
- `client.backups` — catalog resource (list, latest, history, validate, delete)
- Persistent backup catalog via SQLite (WAL mode) under `~/.cache/odoo-instance-sdk/`
- ZIP validation (`is_zipfile`, manifest, CRC) and optional `pg_restore` validation
- Audited download with UUID tracking and `.part` files for atomic completion
- `BackupEvent`, `BackupState`, `BackupFormat`, `BackupValidationStatus` enums

### Removed (breaking)
- `client.database` — use `instance.databases`
- `client.server` — use `instance.start()` and lifecycle methods on `OdooInstance`
- `BackupArtifact` — replaced by `Backup`
- `RemoteInstanceError` — renamed to `NonLocalInstanceError`
- HTTP Basic Auth — `master_pwd` sent only as form field

### Changed
- `OdooClientConfig` carries only `executable`, `backups_directory` (optional) and `http_timeout_seconds`; each `OdooInstance` provides its own `base_url` and master password
- Process registry shared across instances on `OdooClient`
- `warn_if_cleartext_auth` → `warn_if_cleartext_secret`

### Security
- Destructive operations (`restore`, `drop`) guarded by `NonLocalInstanceError`
- `MasterPasswordRequiredError` raised early when password is missing
- Backup filenames start with the backup UUID; paths contained within the destination directory

## [0.1.0] - 2026-07-13

### Added
- Initial SDK release with core functionality
- Server lifecycle management (start, stop, status, run, wait_ready)
- Database operations (backup, restore, list, drop, exists)
- Readiness checks via HTTP health endpoint (`/web/health`)
- Local-only guard for destructive operations (restore, drop)
- Comprehensive type hints with strict mypy validation
- CI workflow (ruff + mypy) on push and PR
- Examples: backup/restore script, FastAPI integration

### Security
- Master password and database password masked in repr/logs
- Destructive operations refuse non-local base URLs (localhost, 127.0.0.0/8, ::1)
- HTTP interface defaults to 127.0.0.1 (loopback only)
- One-time warning when Basic Auth is sent over unencrypted HTTP for non-local hosts
- Path-traversal protection in backup() — database name validated
- master_pwd redacted from DatabaseError.body

### Changed
- `database.backup()` uses POST form-data (was GET)
- `database.restore()` uses multipart with `backup_file` field
- `database.list()` uses JSON-RPC format
- `server.stop()` honors the `timeout` parameter (was hardcoded 100ms)
- HTTPStatus enum used for status code checks (was magic numbers)