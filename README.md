# odoo-instance-sdk

[![CI Status](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

A typed Python SDK for managing local Odoo 19.0 instances: process lifecycle, CLI commands, readiness checks, and database operations via standard HTTP API.

## Installation

> **Note:** not yet published to PyPI.

From Git (recommended until first PyPI release):

```bash
uv add "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk"
```

Pin to a tag or commit for reproducible installs:

```bash
uv add "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk@v0.1.0"
```

## Quick start

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig

config = OdooClientConfig(
    executable="odoo",
    base_url="http://localhost:8069",
    master_pwd="admin",
)
client = OdooClient(config)
```

### Start a local Odoo server

```python
from odoo_instance_sdk import StartConfig

proc = client.server.start(StartConfig(
    http_port=8069,
    db_host="localhost",
    db_user="odoo",
    db_password="odoo",
))

# Wait until the server is ready
result = client.server.wait_ready(proc, timeout=60.0)
print(f"Ready: {result.ok} in {result.elapsed:.1f}s")
```

### Run a one-shot CLI command

```python
result = client.server.run(["--version"], timeout=10.0)
print(result.stdout)
print(f"Exit code: {result.returncode}, took {result.duration:.2f}s")
```

### Backup and restore databases

```python
# Back up — works on local and remote instances
artifact = client.database.backup("mydb", format="zip", include_filestore=True)
print(f"Saved to: {artifact.path}")

# Restore — local-only, guarded by SDK
restored = client.database.restore(artifact, "mydb_copy")
print(f"Restored as: {restored.new_db}")
```

## API overview

```
OdooClient
├── server
│   ├── run()        — one-shot CLI command
│   ├── start()      — long-lived Odoo process
│   ├── stop()       — graceful then forced termination
│   ├── status()     — running / exited
│   └── wait_ready() — poll /web/health until pass
└── database
    ├── backup()     — download backup (local or remote)
    ├── restore()    — upload backup (local-only)
    ├── drop()       — delete database (local-only)
    ├── list()       — list databases
    └── exists()     — check database exists
```

See the docstrings on each model and method for full type and parameter details. The `StartConfig` fields map directly to Odoo 19.0 CLI options.

## Security

See [SECURITY.md](SECURITY.md) for the security model and how to report vulnerabilities.

Key points:
- `master_pwd` is never in `repr`, exception messages, or logs
- Destructive operations (`restore`, `drop`) are local-only and cannot be bypassed
- HTTP interface defaults to loopback only

## Examples

- [`examples/prepare_dev_instance.py`](examples/prepare_dev_instance.py) — back up from test, start local Odoo, restore, stop

## License

MIT
