# odoo-instance-sdk

[![CI Status](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

A typed Python SDK for managing local Odoo 19.0 instances: process lifecycle, CLI commands, readiness checks, database operations, and an audited local backup catalog.

## Installation

From Git (recommended until first PyPI release):

```bash
uv add "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk"
```

## Quick start

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig

config = OdooClientConfig(executable="odoo")
client = OdooClient(config)

# From odoo.conf
instance = client.instance.from_config("./odoo.conf", base_url="http://localhost:8069")
```

### Start a local Odoo server

```python
from odoo_instance_sdk import StartConfig

instance = client.instance(base_url="http://localhost:8069", master_password="admin")
proc = instance.start(StartConfig(http_port=8069))

result = instance.wait_ready(proc, timeout=60.0)
print(f"Ready: {result.ok} in {result.elapsed:.1f}s")
```

## CLI output and environment inventory

The installed `odcli` command keeps `odoo_instance_sdk.cli:cli` as its stable
Click entry point. Structured output is selected locally on supported leaves
with `--format rich|json|toon`; Rich is the default. The existing `--json`
option is an alias for `--format json`. Supplying `--json` with `--format json`
is accepted; other combinations are usage errors.

The format option is available on `init`, `doctor`, `env checkout`, `env list`,
`env remove`, `env sync`, `eval`, `exec`, `module list`, `module update`,
`module test`, `translations export`, `deps verify`, `vscode generate`, and
`postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`.
TOON is a machine-readable single-document output mode for these commands;
JSON and TOON contain the same sanitized envelope data.

`odcli env list --watch --interval 2.0` refreshes the Rich inventory in the
foreground. Watch mode requires an interactive TTY and rejects machine output;
the interval must be at least `0.1` seconds. Rich `--all` includes removed
environments. JSON/TOON `--all` retain the active-only compatibility behavior.
The root command, `run`, interactive shell (`shell`), and `logs --follow` intentionally
do not accept document formats or a Rich live wrapper.

The public monitor snapshot uses additive schema v2 fields `observed_port` and
`artifacts`; existing version-1 fields retain their meanings. The CLI envelope
version remains independent and is still v1.

### Database operations

```python
# List databases — returns Database objects with backup info
dbs = instance.databases.list()
for db in dbs:
    if db.backup.format is not None:
        print(f"{db.name} → restored from {db.backup.downloaded_at}")
    else:
        print(f"{db.name} → no restore mapping")

# Positional indexing
db = instance.databases[0]
print(db.name, db.backup.downloaded_at)

# Current database (from configured_database_names[0])
current = instance.databases.current()

# Backup — works on local and remote instances
backup = instance.databases.backup("mydb")
print(f"Saved to: {backup.filename}")

# Restore — local-only, guarded by SDK; writes restore-mapping for from_config() instances
restored = instance.databases.restore(backup, "mydb_copy", copy=True)
print(f"Restored as: {restored.new_db}")

# Drop — local-only; records dropped event for from_config() instances
result = instance.databases.drop("mydb_copy")
```

### Browse the backup catalog

```python
# List all available backups
for b in client.backups.list():
    print(f"{b.database_name} — {b.filename} ({b.size_bytes} bytes)")

# Latest backup for a specific database
latest = client.backups.latest(source_base_url="http://localhost:8069", database_name="mydb")

# Full history for a database
for event in client.backups.history(source_base_url="http://localhost:8069", database_name="mydb"):
    print(f"{event.event_type.value}: {event.message}")
```

### Validate a backup

```python
result = client.backups.validate(backup)
print(f"Valid: {result.valid}, errors: {result.errors}")
```

## API overview

```
OdooClient
├── instance
│   ├── __call__(base_url, master_password=None) -> OdooInstance
│   └── from_config(path, base_url=None, master_password=None) -> OdooInstance
└── backups
    ├── list(...)
    ├── latest(...)
    ├── history(...)
    ├── validate(...)
    └── delete(...)

OdooInstance
├── base_url
├── configured_database_names
├── databases
│   ├── backup(...)
│   ├── restore(...)
│   ├── drop(...)
│   ├── list()
│   ├── exists()
│   ├── current()
│   └── [n]
├── run(args, *, cwd=None, env=None, timeout=None) -> CommandResult
├── start(config: StartConfig, ...) -> OdooProcess
├── stop(proc, *, timeout=10.0)
├── status(proc) -> ProcessStatus
└── wait_ready(proc, *, timeout=60.0) -> ReadinessResult
```

## Cache layout

Backup files and audit metadata are stored under `~/.cache/odoo-instance-sdk/`:

```
~/.cache/odoo-instance-sdk/
├── backups/
│   └── <backup-uuid>_<safe-content-disposition-filename>
└── backups.sqlite3   (SQLite, WAL mode)
```

- Backup filenames begin with the backup UUID and stay within the destination directory.
- Catalog is a persistent SQLite database with full audit history.
- Schema version 2: `backups` + `backup_events` tables (audit), `restores` + `database_events` tables (restore-tracking), with foreign keys.
- Concurrent access uses WAL mode and 5-second busy timeout.
- Catalog file and WAL/SHM sidecars are `chmod 0600`.

## Validation semantics

- **ZIP validation** (always available): checks `is_zipfile()`, required root members (`manifest.json`, `dump.sql`), `testzip()` CRC verification, and `manifest.json` JSON parse.
- **Dump validation** (requires `pg_restore` in PATH): runs `pg_restore --list` against the file with a 60s timeout.
- `raise_if_unavailable=True` raises `BackupValidationUnavailableError` when pg_restore is not found.

## Readiness checks

- GET `/web/health?db_server_status=true` with `httpx.Client(timeout=...)`.
- No Basic Auth — endpoint has `auth="none"` in Odoo 19.0.
- `wait_ready()` polls until the endpoint returns 200 or the linked process exits.
- Configurable timeout (default 60s) and poll interval (default 1.0s).

## Security

- `master_pwd` and `db_password` are never in `repr`, exception messages, or logs.
- Destructive operations (`restore`, `drop`) are local-only and cannot be bypassed.
- HTTP interface defaults to loopback only.
- Basic Auth removed: `master_pwd` is sent only as a form field in POST bodies.
- Cleartext warning fires once per process when master password is sent over HTTP to non-local hosts.

## Development

Requires Python 3.12+ on POSIX (Linux/macOS).

```bash
uv sync --frozen --dev --extra dashboard
git config core.hooksPath .githooks
make pr
```

`make pr` runs lint, types, coverage and compatibility suites, dashboard unit/build checks, the mandatory monitor API smoke gate, and package checks. Live Odoo is opt-in via `make live`; monitor smoke is not opt-in. See [CONTRIBUTING.md](CONTRIBUTING.md) for markers, targeted runs, mutation, and package prerequisites.

## Examples

- [`examples/prepare_dev_instance.py`](examples/prepare_dev_instance.py) — back up from test, start local Odoo, restore, stop

## License

MIT
