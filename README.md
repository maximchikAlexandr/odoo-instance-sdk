# odoo-instance-sdk

`odoo-instance-sdk` gives Odoo 19 developers one typed Python API and one
CLI, `odcli`, for repeatable local environments. It manages Git worktrees,
Python environments, Odoo processes, databases, an optional SDK-owned
PostgreSQL cluster, backups, and local observability without hiding the
underlying Odoo and PostgreSQL tools.

## Concepts

- A **Project** is a repository with an `.odcli/project.toml` manifest that
  declares Odoo, Python, database, and runtime defaults.
- An **Environment** is a registered development checkout: its own worktree,
  generated config, Python environment, port, and database policy.
- An **Odoo instance** is the typed SDK handle for one local or remote Odoo
  endpoint and its process and database operations.
- A **PostgreSQL cluster** is either an external cluster reused by the project
  or an SDK-owned Docker Compose cluster with explicit image trust.

## Requirements and installation

Use Python 3.12 or newer. Git and `uv` are needed for environment workflows;
Docker Compose is needed only for SDK-owned PostgreSQL, and Node.js is needed
only when developing the bundled dashboard.

```bash
uv tool install odoo-instance-sdk
odcli --help
```

For the monitor dashboard, install the optional extra in a project environment:

```bash
uv add 'odoo-instance-sdk[dashboard]'
uv run odcli monitor
```

## CLI-first quick start

Run these commands from the Odoo project repository. `init` writes the project
manifest; inspect the plan before creating a feature environment.

```bash
odcli init --odoo-bin ./odoo/odoo-bin --python 3.12 --config ./odoo.conf
odcli doctor
odcli env checkout feature/customer-credit --dry-run
odcli env checkout feature/customer-credit
odcli --env feature/customer-credit run
odcli --env feature/customer-credit logs
odcli env list
```

Global selectors such as `--project` and `--env` belong before the subcommand.
Exact flags are intentionally delegated to executable help, for example
`odcli env checkout --help`. Structured output is leaf-local, not a root command
promise: commands that support it expose `--format rich|json|toon` and/or
`--json`. Supplying `--json` with `--format json` is allowed where both are
documented. TOON is the compact structured form.

All bounded mutating or process-backed leaves support the same inspect-first
shape: add `--dry-run` to render the captured typed plan, then omit it to run
the command. The preview contains ordered process/action steps, redacted
arguments and stdin, planning observations, warnings, classifications, and a
fingerprint. A preview never launches a process, prompts, or mutates the
workspace. See [execution boundary and CLI inventory](docs/execution-boundary.md)
for the complete eligibility table and the intentionally narrow exceptions.

## Common workflows

### Tests, modules, dependencies, translations, and VS Code

```bash
odcli test --changed
odcli module list
odcli module update sale
odcli module test sale
odcli deps verify
odcli translations export sale
odcli vscode generate
```

`test --changed` selects add-ons from the Git diff. Module commands provide an
explicit module-oriented path; dependency verification, translation export,
and VS Code generation remain separate inspectable operations.

### PostgreSQL trust and lifecycle

An SDK-owned cluster will not start an unapproved mutable image. Resolve and
approve the image digest, then start and inspect the cluster:

```bash
odcli postgres approve-image
odcli postgres up
odcli postgres status
odcli postgres stop
```

External PostgreSQL remains externally managed; `status` reports it but `stop`
does not take ownership of it.

### Prepare a project database

Database refresh can use a pinned remote test instance while keeping its
master password outside the manifest. This complete example refreshes backups
older than 24 hours and verifies branch provenance against `main`:

```toml
[project]
default_base_ref = "main"
refresh_after_hours = 24.0

[test_instance]
base_url = "https://odoo-test.example"
database = "testdb"
git_branch = "main"
```

```bash
odcli --env feature/customer-credit db refresh
odcli --env feature/customer-credit db reset-admin-password
```

Refresh follows the environment's configured database policy. Destructive
database operations are local-only and validate provenance before mutation.

### Automation and shell access

```bash
odcli --env feature/customer-credit eval 'env.user.search_count([])'
odcli --env feature/customer-credit exec scripts/check_data.py
odcli --env feature/customer-credit shell
odcli --env feature/customer-credit logs --follow
```

The interactive shell requires an interactive TTY. `logs --follow` stays
attached until interrupted.

Native `run` and interactive `shell` retain their normal foreground/TTY
streams, but expose the same bounded preview contract:

```bash
odcli --env feature/customer-credit run --dry-run
odcli --env feature/customer-credit run --dry-run --format json
odcli --env feature/customer-credit shell --dry-run --format toon -- --dev
```

`--json` is the shorthand for `--format json`; both forms serialize the same
captured plan. Supplying either output option without `--dry-run` is rejected
by Click with exit `2` before SDK resolution or process launch. `logs
--follow`, the monitor server, and normal interactive shell/run streams are
documented native transports because they are intentionally unbounded or
interactive rather than finite plan documents.

## Complete CLI command reference

This is the complete shipped command-path inventory. The Click object
`odoo_instance_sdk.cli:cli` is its source of truth. Every entry has one purpose
sentence; use the entry's `--help` for exact options.

<!-- cli-command-inventory:start -->
- `odcli init` — Create or update the project manifest from explicit inputs.
- `odcli doctor` — Diagnose the resolved project, runtime, and PostgreSQL setup.
- `odcli env checkout` — Plan or create an isolated branch worktree and environment.
- `odcli env list` — List registered environments, active-only unless `--all` is requested.
- `odcli env remove` — Remove a registered environment and its owned artifacts safely.
- `odcli env sync` — Rebuild or synchronize an environment's Python dependencies.
- `odcli run` — Start resolved Odoo in the foreground for the selected environment.
- `odcli logs` — Read retained Odoo logs, optionally following new output.
- `odcli shell` — Open an interactive Odoo shell in the selected environment.
- `odcli eval` — Evaluate one Python expression through the Odoo shell boundary.
- `odcli exec` — Execute a Python script through the Odoo shell boundary.
- `odcli test` — Select and run Odoo tests, including changed-add-on selection.
- `odcli module list` — Discover installable modules visible to the environment.
- `odcli module test` — Run tests for explicitly named modules.
- `odcli module update` — Upgrade explicitly named modules in the selected database.
- `odcli translations export` — Export translations for a selected module and languages.
- `odcli deps verify` — Verify Python and add-on dependency readiness.
- `odcli vscode generate` — Generate VS Code launch configuration from project settings.
- `odcli postgres approve-image` — Pin trust to the resolved PostgreSQL image digest.
- `odcli postgres status` — Report the configured PostgreSQL cluster state and endpoint.
- `odcli postgres up` — Start or verify the configured PostgreSQL cluster.
- `odcli postgres stop` — Stop an SDK-owned PostgreSQL cluster without deleting its volume.
- `odcli db refresh` — Refresh an environment database from its configured source policy.
- `odcli db reset-admin-password` — Reset the Odoo administrator password in the selected database.
- `odcli monitor` — Serve local environment snapshots in headless or dashboard mode.
<!-- cli-command-inventory:end -->

## Python SDK

The public SDK mirrors the same resources without going through Click. Start
with `OdooClient`, create an instance directly or from `odoo.conf`, and use its
typed `databases`, process lifecycle, backup catalog, environment, monitor, and
PostgreSQL resources.

```python
from odoo_instance_sdk import OdooClient, OdooClientConfig

client = OdooClient(config=OdooClientConfig(executable="odoo-bin"))
instance = client.instance.from_config("./odoo.conf")

for database in instance.databases.list():
    print(database.name, database.backup)
```

See [Python SDK examples](docs/python-sdk.md) for runnable examples covering
database backup/restore, processes, environments, PostgreSQL, monitoring, and
inspect-then-run command siblings. The complete boundary inventory and
allowlist rationale are in [docs/execution-boundary.md](docs/execution-boundary.md).

## Monitor and local API

```bash
odcli monitor --headless
odcli monitor --watch --interval 2
```

The monitor binds to loopback only. The dashboard is an optional extra; the
headless server does not require its static assets. Stable routes are:

- `GET /healthz` — process health.
- `GET /api/v1/snapshot` — the current typed environment snapshot, including
  `observed_port` and `artifacts`.
- `POST /api/v1/pgadmin/open` — UI-only pgAdmin launch, protected by same-origin,
  CSRF, and JSON request checks.

The default environment inventory is active-only. Use explicit include-removed
options where supported. Monitor snapshots isolate component failures and never
publish stored secrets or absolute catalog paths.

## Security and data location

Secrets are never written to the project manifest. Generated secret config and
PostgreSQL credentials use restricted user-data files; the backup catalog uses
the platform cache directory. Remote destructive database operations are
rejected, and cleartext remote authentication emits a warning.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and verification, the
[changelog](CHANGELOG.md) for shipped behavior, and
[GitHub Issues](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues)
for defects and roadmap proposals. Roadmap issues describe planned work, not
features shipped by the current package.

## License

[MIT](LICENSE)
