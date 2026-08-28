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

The format option is available on `init`, `doctor`, `env checkout`, `env list`, `test`,
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

### Run local Odoo tests

`odcli test [TARGET]` resolves one ready environment first, then selects tests
only from the generated, worktree-contained `addons_path` roots. A bare module,
the current directory, or one test file can be selected:

```bash
odcli --env feature-env test sale
odcli --env feature-env test                 # nearest addon for the current directory
odcli --env feature-env test addons/sale/tests/test_sale_order.py
odcli --env feature-env test sale --tags '/sale:TestSale.test_confirm'
```

Module and cwd selection default to the native `/<module>` tag. A file defaults
to the native `/<module>/tests/<path>` tag. `--tags` is passed byte-for-byte for
module, cwd, and changed selection; a file target cannot be combined with
`--tags`. Targets are singular, and a target cannot be combined with
`--changed`.

Changed-addon selection uses only direct worktree changes and accepts an
explicit base or the environment's recorded non-`HEAD` base:

```bash
odcli --env feature-env test --changed --base origin/dev --dry-run --format json
odcli --env feature-env test --changed --tags '/sale,/stock'
```

The changed plan is deterministic: modules are sorted and deduplicated, and
without explicit tags their selector is joined as `/sale,/stock`. Changes
outside configured addon roots are a successful `no_addon_changes` result;
unsafe paths inside an addon root are reported as fatal unmapped paths. External,
missing, and symlinked addon roots are never selected, and reverse dependants
are not inferred.

`--dry-run` is available only with `--changed`; it reports environment, Git, and
selection provenance without contacting PostgreSQL or starting Odoo. An actual
run performs a read-only check against exactly one configured database and
requires every selected module to be installed before invoking Odoo's native
test runner. The command never installs, updates, restores, commits, or otherwise
mutates application data. Rich is the default; `--format json|toon` returns the
same envelope data, and execution counts are omitted from dry-run and
`no_addon_changes` results.

The compatibility alias retains its plural parser and required native tags:

```bash
odcli --env feature-env module test sale stock \
  --test-tags '/sale,/stock' --reload-tests --allow-empty
```

`module test` uses the same safe selection boundary, read-only preflight,
single native runner, typed result, and `--format`/`--json` output contract.

The public monitor snapshot uses additive schema v2 fields `observed_port` and
`artifacts`; existing version-1 fields retain their meanings. The CLI envelope
version remains independent and is still v1.

### Prepare a project database

Projects may declare a remote test instance in `.odcli/project.toml`:

```toml
[project]
default_base_ref = "main" # optional; checkout falls back to HEAD
refresh_after_hours = 24 # optional; must be finite and greater than zero

[test_instance]
base_url = "https://odoo-test.example"
database = "testdb"
git_branch = "main" # optional; --source-branch overrides it
```

Set the remote instance master password only in the process environment. It is
read from `ODCLI_TEST_MASTER_PASSWORD` for an explicit preparation request and
is never stored in the manifest or emitted by the SDK.

Repository-selected preparation also requires an external exact-origin approval
for every non-loopback test instance. Set the sole approval variable to a
comma-separated list of non-secret canonical origins before refreshing, for
example:

```bash
export ODCLI_TEST_INSTANCE_ORIGIN_PINS="https://odoo-test.example:443,https://staging.example:8443"
odcli --project /path/to/project db refresh
```

Entries are normalized to lowercase scheme/host plus effective port; paths,
queries, fragments, wildcards, and host-only entries do not broaden approval.
Each origin used by the repository's `[test_instance]` flow must match one pin.
Loopback origins do not need a pin, while non-loopback HTTP is always rejected.
The SDK checks transport and approval before preparation locking, PostgreSQL or
database-manager work, HTTP, catalog, or manifest mutation. The variable is
never persisted and does not contain a password. This repository approval is
limited to project preparation; direct `instance.databases.backup()` retains
its generic behavior and does not require a repository pin.

```bash
odcli db refresh --format json
odcli db refresh --restore --source-branch release/19
odcli db refresh --restore --reset-admin-password
odcli db reset-admin-password --format toon
```

`db refresh` accepts an explicit `--project`, the nearest project manifest, or
an exact registered worktree. A restore chooses a unique local target, records
the source branch, and switches the project default only at the final manifest
commit point. `--reset-admin-password` is allowed only with `--restore`; it
does not accept a password option or prompt. Checkout freshness may invoke the
same private preparation coordinator when `refresh_after_hours` is configured,
but checkout never performs an automatic administrator-password reset.

If a download, restore, reset, mapping, concurrency, or manifest-switch step
fails, the old default remains selected and the downloaded backup/new database
are retained for inspection or manual cleanup. A backup whose recorded branch
is unknown can be used for the current call only with an explicit
`--source-db`; inferred sources fail closed and report the `--source-db` or
refresh guidance. Use JSON or TOON for scripts; both are single, ANSI-free
envelopes with the same public fields and diagnostics.

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
