# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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