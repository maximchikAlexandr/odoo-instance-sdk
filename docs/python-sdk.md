# Python SDK examples

These examples use only the public `odoo_instance_sdk` package. They are
syntax-checked and import-checked in the offline test suite. Operations that
connect to Odoo, Git, Docker, or PostgreSQL require the corresponding local
service and configuration. Global SDK state is stored below `~/.odcli`; the
first catalogue-backed operation migrates legacy platformdirs locations with a
locked, journaled, retry-safe migration. Repository-local `.odcli` manifests
are separate project data and are never migrated into that global root.

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

For a native foreground Odoo run, pass a sequence through the keyword-only
`args` parameter. It is frozen into the one captured process step, so the
preview and execution keep identical boundaries, order, and repeated values:

```python
native = instance.run_foreground_command(
    args=("--dev=reload", "--log-level", "debug", "--stop-after-init")
)
print(native.plan)       # redacted native argv
result = native.run()    # inherited stdin/stdout/stderr, native exit code
print(result)
```

The CLI spelling is `odcli run -- --dev=reload`; the literal `--` delimiter is
required for non-empty native argv. `--dry-run` previews without recording use
or launching Odoo. SDK and CLI validation rejects managed config and
database/credential, addons/upgrade/data-path, HTTP/gevent/longpolling bind or
port, and logfile option families; ordinary Odoo flags such as repeated
`--dev`, `--log-level`, and terminating `--stop-after-init` pass unchanged.

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

Database diagnostics use the instance-bound cluster and a shared resolver. An
explicit database name changes only the database while host, port, user, and
cluster ownership remain bound to the instance:

```python
instance = client.instance.from_config("./odoo.conf")
database = instance.databases
locks = database.locks("postgres", top=20)
stats = database.stats("postgres", top=20)
bloat = database.bloat("postgres", top=20, exact_max_scan_mb=64)

print(locks.rows)
print(stats.summary.database_bytes, stats.warnings)
print([(row.table, row.method) for row in bloat.tables])
```

`stats` bytes are numeric and its counters are cumulative; cache fields are
nullable only when the cache capability cannot be measured. Bloat estimates
are bounded by default and exact scans are opt-in and bounded. Results are
frozen typed models with closed warnings/capabilities, so JSON and TOON CLI
projections preserve the same values and failure exit semantics.

`init_monitoring("postgres")` is a mutating, SDK-owned-cluster operation and
must be explicitly confirmed at the CLI with `--yes`. It reports sorted,
disjoint `installed`, `already_present`, and privilege/availability `skipped`
extensions; an unavailable extension is never created. A dry-run is inert and
redacts secrets. For native terminal use, `database.psql(("-c", "SELECT current_database();"))`
delegates to native `psql`, preserving its streams,
signals, and exit code. Native `psql` does not accept document formatting;
the SDK removes ambient `PGOPTIONS` and never exposes the password in plans or
errors.

## CLI resource diagnosis and lifecycle safety

Resource inventory and doctor are intentionally CLI-private read-only
projections; no `ResourceInventory` method is added to the public SDK. Use the
CLI from an initialized project when local ownership and storage evidence is
needed:

```bash
odcli resource ls --format json
odcli resource doctor --format toon
```

Both commands combine typed observations without reconciling catalogue state,
deleting files, or stopping processes. Paths and diagnostics are sanitized;
logical PostgreSQL size is labeled separately from measured host bytes, and
unknown ownership stays retained. For remote backup acquisition, the CLI
streams into an exclusive temporary file and publishes only after checksum,
size, fsync, and close succeed. A failed or interrupted transfer closes its
handles and retains any already-published backup.

Use `db restore UUID --dry-run` to inspect a local restore before confirmation.
The default changes only after restore, neutralization, postcondition, audit,
and optional admin reset complete. Ctrl-C returns exit `130`; JSON/TOON keep
one progress-free document on stdout and put sanitized diagnostics on stderr.
The public SDK exposes these typed operations directly while CLI-only resource
diagnosis remains CLI-private.

## Modules and translations

Module discovery is exposed from an instance and reads safe literal manifests
on demand. It does not execute `__manifest__.py` or persist a second catalogue:

```python
for module in instance.modules.catalogue():
    print(module.name, module.path, module.depends)

order = instance.modules.install_order(("sale",))
print(order.modules)
```

The CLI adds `module info`, `module where`, `module deps`, and
`module install-order`; `module update --changed` reuses the captured changed
file selection and fails closed for unmapped or not-installed modules.
Translation export remains a CLI workflow. It optionally validates generated
PO input with an absolute `msgfmt` executable and `LC_ALL=C`; an unavailable
tool is reported as a typed result and is never installed as a package
dependency. Publication is atomic and `--dry-run` does not write output.

## Git workflow

The public instance exposes a concrete tracker-neutral Git resource. It uses
staged files, the shared immutable command boundary, and the module catalogue
for scope inference:

```python
context = instance.git.commit_context(
    "describe the staged change", ticket="PROJ-123", tag="DOC"
)
print(context.message)

check = instance.git.check(base="main")
print(check.valid, check.issues)

commit = instance.git.commit_command(context)
print(commit.plan)
```

`git absorb` is an optional captured `git-absorb` adapter. `git sync` fetches,
rebases, validates the result, and can publish only the same branch to an SSH
`origin`; a stale remote lease or conflict fails without an automatic retry.
Configured ticket links use `ticket_link_enabled` and `ticket_base_url` in the
project manifest. Historical vendor-specific settings are rejected or require
actionable migration, and no external tracker or forge client is contacted.

Machine-readable CLI output uses the explicit leaf-local `--format json` or
`--format toon` selector; the removed `--json` option is not a compatibility
alias. Eligible bounded results may additionally use typed dotted `--fields`
projection. Machine documents and `env path` preserve absolute paths; only
human Rich presentation may shorten paths beneath `HOME`.

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
