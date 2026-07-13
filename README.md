# odoo-instance-sdk

A typed Python SDK for managing local Odoo 19.0 instances: process lifecycle, CLI commands, readiness checks, and database operations via standard HTTP API.

## Installation

```bash
uv add odoo-instance-sdk
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

### Stop the server

```python
client.server.stop(proc, timeout=10.0)
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

### List, check, and drop databases

```python
databases = client.database.list()
print(f"Databases: {databases}")

if client.database.exists("mydb"):
    result = client.database.drop("mydb")
    print(f"Dropped: {result.db}")
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

### Local-only guard

`restore()` and `drop()` refuse non-local `base_url` (`localhost`, `127.0.0.0/8`, `::1` only). The check runs before any HTTP request and cannot be disabled.

### Security

- `master_pwd` never appears in `repr`, exception messages, or logs.
- `db_password` is masked in `StartConfig` and `OdooProcess` repr.
- `StartConfig.http_interface` defaults to `127.0.0.1` (loopback only).
- Warning on Basic Auth over unencrypted HTTP for non-local URLs.

## Examples

See the [`examples/`](examples/) directory for complete scripts:

- [`examples/backup_and_restore.py`](examples/backup_and_restore.py) — back up production DB, restore onto local staging
- [`examples/fastapi_integration.py`](examples/fastapi_integration.py) — FastAPI service: one `/refresh` endpoint that backs up prod → restores staging → starts the instance

## License

MIT