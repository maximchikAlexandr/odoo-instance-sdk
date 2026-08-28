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

The Python monitor returns typed models. The local HTTP interface exposes the
same snapshot contract at `/api/v1/snapshot`; see the README for its security
and deployment boundaries.
