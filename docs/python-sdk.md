# Python SDK examples

These examples use only the public `odoo_instance_sdk` package. They are
syntax-checked and import-checked in the offline test suite. Operations that
connect to Odoo, Git, Docker, or PostgreSQL require the corresponding local
service and configuration.

## Create a client and instance

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig

client = OdooClient(config=OdooClientConfig(executable="odoo-bin"))
instance = client.instance.from_config("./odoo.conf")
print(instance)
```

For an already-running endpoint, construct the instance directly:

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig

client = OdooClient(config=OdooClientConfig(executable="odoo-bin"))
instance = client.instance("http://127.0.0.1:8069", master_password="from-secret-store")
print(instance.databases.names())
```

## Inspect then run

Finite SDK operations expose an immutable `*_command()` sibling. Inspect its
public plan before calling `.run()`; the private callback, secrets, and exact
executor snapshot are not part of serialization or `repr`.

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig

client = OdooClient(config=OdooClientConfig(executable="odoo-bin"))
instance = client.instance.from_config("./odoo.conf")

command = instance.run_command(["--stop-after-init"], cwd=".")
print(command.plan)       # redacted, ordered ExecutionPlan
print(command.commands)   # captured process steps only
result = command.run()    # runs that same captured snapshot
print(result.returncode)
```

The same pattern applies to `instance.start_command()`,
`run_foreground_command()`, `shell_command()`,
`run_shell_script_command()`, and `stop_command()`; to
`client.environments.checkout_command()`, `sync_python_command()`, and
`snapshot_command()`; and to PostgreSQL, database, backup, preparation,
environment-removal, and pgAdmin command siblings. Convenience methods such
as `instance.run()` and `monitor.snapshot()` delegate once to their sibling,
so preview and execution cannot silently rebuild different argv or inputs.

Plans redact passwords, secret-file contents and sensitive paths while
preserving argument boundaries and multiline stdin/source previews. Planning
observations identify read-only probes and their classifications. A changed
Git revision, path, port, database/provenance fact, or other captured
precondition raises a typed stale-plan error before the first mutation; the
command is not replanned or replaced.

`ActionStep` is used for honest in-process effects such as locks, catalog
updates, filesystem changes, signals, and compensation. It is not disguised
shell text. `ProcessStep` is used for captured children and includes the
redacted argv, cwd, environment policy, stdin preview, timeout, and execution
mode.

## Database operations

```python
from pathlib import Path

from odoo_instance_sdk import OdooClient, OdooClientConfig

client = OdooClient(config=OdooClientConfig(executable="odoo-bin"))
instance = client.instance.from_config("./odoo.conf")

database = instance.databases.current()
backup = instance.databases.backup(database.name, destination=Path("./backups"))
validation = client.backups.validate(backup)
print(backup.path, validation.status)
```

Restore and drop are intentionally restricted to local instances:

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig

client = OdooClient(config=OdooClientConfig(executable="odoo-bin"))
instance = client.instance.from_config("./odoo.conf")
backup = client.backups.latest(database_name="source_database")

if backup is not None:
    instance.databases.restore(backup, "restored_database")
    instance.databases.drop("restored_database")
```

## Process lifecycle

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig, StartConfig

client = OdooClient(config=OdooClientConfig(executable="odoo-bin"))
instance = client.instance.from_config("./odoo.conf")
process = instance.start(StartConfig(database="development", http_port=8071))

try:
    readiness = instance.wait_ready(process, timeout=60)
    print(readiness)
finally:
    instance.stop(process)
```

For a command sibling, `Command.run()` returns the native typed result and
preserves captured, inherited, foreground, and long-running stream semantics.
Use the CLI dry-run for a bounded plan; normal foreground and interactive
shell calls intentionally keep their native streams.

## Projects, environments, and PostgreSQL

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig, PostgresCluster, ProjectConfig

project = ProjectConfig.load(".")
client = OdooClient(config=OdooClientConfig(executable="odoo-bin"))
environments = client.environments.list(project.repository_root)
cluster = PostgresCluster.from_project(project)

print([environment.name for environment in environments])
print(cluster.status())
```

Starting an SDK-owned Compose cluster requires prior image approval; external
clusters remain externally managed.

## Snapshot monitoring

```python
from odoo_instance_sdk import EnvironmentMonitor

monitor = EnvironmentMonitor()
snapshot = monitor.snapshot()

for project in snapshot.projects:
    print(project.id, project.name)
```

For a finite, inspectable snapshot use `snapshot_command()`:

```python
command = monitor.snapshot_command()
print(command.plan)  # Git/storage/Docker/PostgreSQL probes are visible
snapshot = command.run()
```

`watch()` is the deliberate unbounded exception: each tick creates and runs a
fresh command, so probes and ledgers are never reused across ticks.

The Python monitor returns typed models. The local HTTP interface exposes the
same snapshot contract at `/api/v1/snapshot`; see the README for its security
and deployment boundaries.

See [execution-boundary.md](execution-boundary.md) for the canonical CLI leaf
inventory, native-stream exceptions, and the checked process/output/type
allowlists.
