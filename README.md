# odoo-instance-sdk

[![CI](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

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

The package is not published on PyPI yet. Install `odcli` as a user-level uv
tool directly from GitHub:

```bash
uv tool install "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
odcli --help
```

For the monitor dashboard, install the optional extra in a project environment:

```bash
uv add "odoo-instance-sdk[dashboard] @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git"
uv run odcli monitor
```

## CLI-first quick start

Run these commands from the Odoo project repository. `init` writes the project
manifest. The main checkout can run directly from that project context; create
an environment only when an isolated feature worktree is useful.

```bash
odcli init --odoo-bin ./odoo/odoo-bin --python 3.12 --config ./odoo.conf
odcli doctor
odcli run --dry-run
odcli run
odcli env create PROJ-123 --dry-run
odcli env create PROJ-123
odcli --env PROJ-123 run -- --dev=reload
odcli --env PROJ-123 logs
odcli env ls
```

`odcli run` resolves context in this order: an explicit `--env`, then an exact
registered worktree, then an explicit `--project` or the nearest initialized
project manifest upward from the current directory. A
project run reads Python, `odoo-bin`, source config, working directory, port,
database, and default run arguments from `.odcli/project.toml`; it does not
create an environment or add the main checkout to `odcli env ls`.

Project-local child variables may be placed in `.odcli/.env`. The file is
ignored by Git, must be owner-readable only (`0600` or another mode with no
group/other bits), and uses a deliberately small grammar: `KEY=value`,
literal single quotes, or double quotes with only `\\`, `\"`, `\n`, `\r`, and
`\t` escapes. Blank/comment lines and empty values are supported; shell
expansion, `export`, duplicate keys, and malformed quoting are rejected. The
invoking process wins per key and `os.environ` is never changed. Ordinary
values are scoped to Odoo children; Git, PostgreSQL, Docker, editors, package
tools, and other children retain their purpose-built environments.

`ODCLI_TEST_MASTER_PASSWORD` is a restore-only secret input. It is consumed
before child creation, removed from every child environment, and never written
to plans, logs, diagnostics, fingerprints, or structured output.
`ODCLI_ADMIN_PASSWORD` is the administrator password for
`db reset-admin-password`, restore with `--reset-admin-password`, and COPY
replacement. Interactive Rich mode prompts twice with hidden input; JSON, TOON,
and `--dry-run` never prompt. Non-interactive mode reads the process environment
first, then `.odcli/.env`. The secret never appears in argv, shell history,
manifest, catalog, plan, Rich/JSON/TOON output, logs, or exception text; the
result reports only the fact and provenance (`prompt` or `environment`). A
missing `.odcli/.env` is valid; unreadable, insecure, or malformed files fail
before work and report only the path (and parser line where applicable).

With `--postgres compose`, init also writes the effective project runtime config
to `.odcli/odoo.conf` with owner-only permissions. It is derived from the
source config without changing its bytes, binds Odoo to the SDK-owned cluster,
and keeps the cluster password out of the manifest and command output.

Self-contained setup in one command uses the additional `init` options below.
They fill `[test_instance]`, select generated `.odcli/odoo.conf` as the
effective local `source_config` under `--local-config`, create `.odcli/.env`
with `ODCLI_TEST_INSTANCE_ORIGIN_PINS` and an empty `ODCLI_TEST_MASTER_PASSWORD=`
under `0600`, and record `data_dir` as `{project_root}/.odcli/filestore` for
Compose-owned filestore provenance. A blocking completeness check lists missing
groups before any write; re-running `init` without test-instance options
preserves an existing valid `[test_instance]`. Interactive Rich mode offers
cancel-vs-partial confirmation when setup is incomplete; `--no-input` fails with
`init_incomplete` unless `--allow-partial` is explicit. `--dry-run` shows
manifest/config/dotenv ActionSteps without writes, secret generation, cluster
mutation, or prompts:

```bash
odcli init --odoo-bin ./odoo/odoo-bin --python 3.12 --postgres compose \
  --test-url https://odoo-test.example --test-branch main --local-config --dry-run
odcli init --odoo-bin ./odoo/odoo-bin --python 3.12 --postgres compose \
  --test-url https://odoo-test.example --allow-partial
```

`odcli --version` appends the first seven characters of a PEP 610 VCS
`commit_id` when the installed package was built from a Git URL; wheel/sdist
installs without `direct_url.json` keep the package version only.

Global selectors such as `--project` and `--env` belong before the subcommand.
Exact flags are intentionally delegated to executable help, for example
`odcli env create --help`. Structured output is leaf-local, not a root command
promise: commands that support it expose `--format rich|json|toon`. The former
`--json` compatibility option is removed and fails before resolution; use
`--format json`. Supplying `--json` with `--format json` is rejected before
resolution. Eligible bounded machine-readable leaves also support typed
`--fields a.b,c` projection with an explicit JSON or TOON format. The field
schema comes from that leaf's typed result, and Rich output is never
field-filtered. TOON is the compact structured form.

To enter a registered environment worktree, use ordinary shell command substitution: `cd "$(odcli env path <environment>)"`.

All bounded mutating or process-backed leaves support the same inspect-first
shape: add `--dry-run` to render the captured typed plan, then omit it to run
the command. The preview contains ordered process/action steps, redacted
arguments and stdin, planning observations, warnings, classifications, and a
fingerprint. A preview never launches a process, prompts, or mutates the
workspace, except for `update`: its read-only resolver and preflight may run
to freeze the exact target before the install/migration plan is rendered.
Those mutation steps remain inert. See [execution boundary and CLI inventory](docs/execution-boundary.md)
for the complete eligibility table and the intentionally narrow exceptions.

Rich previews show the exact sanitized captured commands in execution order;
identified progress lines use the captured step ID, operation, target, elapsed
time, and process exit status. Absolute timestamps in Rich output are rendered
in the invoking user's local timezone; JSON and TOON retain the original
UTC/ISO-8601 values. For example:

```bash
odcli env create PROJ-123 --dry-run
odcli db restore 01234567-89ab-cdef-0123-456789abcdef --dry-run
odcli db restore --file ./backup.zip --target restored_db --dry-run
```

### Lifecycle and recovery contracts

The current release includes the nine GitHub #61 contracts below. They all
reuse the existing captured-command, catalogue, locking, redaction, and
confirmation boundaries:

- Owned `env create --create-venv` runs one bounded `<python> <odoo-bin>
  --help` readiness probe before `ready`; shared runtimes do not receive that
  probe.
- COPY `env rm` drops only the proven target through guarded PostgreSQL
  ownership checks, does not depend on an Odoo listener or HTTP-port state, and
  retries retained `cleanup_failed` evidence idempotently.
- Canonical aliases (`env create|ls|rm`, `backup ls|inspect|rm`, `db ls|rm`,
  `postgres ps`, `resource ls`, and `module ls`) are the same Click operations
  as their retained spellings, with one command ID and one output contract.
- Monitor schema v4 exposes one nullable `memory_bytes` value: Darwin uses
  validated physical footprint and other supported platforms use the existing
  RSS total. Rich, JSON, TOON, OpenAPI, and the dashboard read that same field.
- Success and failure documents preserve the resolved `dry_run` value; a
  preview never launches a child, prompts, or mutates state, except that
  `update` may run its read-only resolver and preflight before showing the
  inert install/migration plan.
- `odcli db restore BACKUP_UUID --replace` replaces only a selected stopped
  COPY environment. Ordinary `db restore` also accepts `--file PATH` for a
  caller-owned Odoo ZIP; exactly one of `BACKUP_UUID` and `--file` is required,
  and `--replace` remains catalogue-only. Both sources preserve the exact
  target and environment identity, use matching database/filestore
  provenance, support the existing `--reset-admin-password`, and retain
  sanitized retry evidence on incomplete compensation.
- Top-level `odcli stop` signals only a runtime whose persisted owner and live
  executable, argv, create time, cwd, config, and (on POSIX) process-group
  identity still match; stale or inaccessible evidence fails closed.
- Ticket Allocation accepts a tracker-shaped key such as `PROJ-123`, allocates
  the next never-reused branch from local refs, catalogue history (including
  removed rows), and recorded `origin` heads, then creates it from the selected
  `--base` without fetch, ticket validation, or network configuration.
- Applied settings are versioned, normalized, and secret-free. `odcli doctor`
  reports field-specific `in_sync`, `drifted`, or `unknown` results without
  repairing or mutating anything; dependency and managed-config formatting
  that is semantically unchanged remains synchronized.

`.localhost` browser-session isolation is separate research and is not part of
this delivery. Use `odcli env show` for a focused selected-environment
projection, and use `odcli doctor` for read-only drift diagnosis.

`odcli env create` (also spelled `odcli env checkout`) accepts a Ticket key
(for example `PROJ-123`) rather than a branch name. It reserves `PROJ-123` or
the next unused `PROJ-123_N` from local refs, retained catalogue history
(including removed environments), and current `origin` heads, then creates
the worktree from the selected `--base`. The generated environment name is
`<project>:<resolved-branch>`; the CLI does not expose a separate `--name`
override. Both spellings are the same Click operation and no external tracker
client is contacted.

Catalogue lists are project-scoped by default. Use `--all-projects` when a
global result is intended, for example `odcli backup ls --all-projects` or
`odcli resource ls --all-projects`; Rich tables format byte values for
people while JSON/TOON retain exact integer bytes. Project HTTP endpoints use
the same precedence everywhere: explicit override, `preferred_http_port`, the
effective `odoo.conf`, then the default port.

## Common workflows

### Tests, modules, dependencies, translations, and VS Code

```bash
odcli test --changed
odcli module ls
odcli module update sale
odcli module test sale
odcli deps verify
odcli translations export sale
odcli vscode generate
```

`test --changed` selects add-ons from the Git diff. Module commands provide an
explicit module-oriented path; dependency verification, translation export,
and VS Code generation remain separate inspectable operations. When a module
update fails behind a long startup log, `module update` prioritizes a valid
nonce-framed `user_error` or `finalization_error` payload and otherwise falls
back to a bounded redacted tail of `stderr`, not the first characters. Rich,
JSON, and TOON return the same stable error code and safe details.

### PostgreSQL trust and lifecycle

An SDK-owned cluster will not start an unapproved mutable image. Resolve and
approve the image digest, then start and inspect the cluster:

```bash
odcli postgres approve-image
odcli postgres up
odcli postgres ps
odcli postgres stop
```

External PostgreSQL remains externally managed; `status` reports it but `stop`
does not take ownership of it.

### PostgreSQL diagnostics and native `psql`

Diagnostics resolve the selected environment/database before any process is
planned.  When no database is given, a registered worktree binding or one
unambiguous project default is used; an ambiguous or missing binding fails
before spawn.  The read-only leaves share one typed transport:

```bash
odcli db locks --format json
odcli db stats --format toon
odcli db bloat --exact-max-scan-mb 64
odcli db init-monitoring --yes
odcli psql -c 'SELECT current_database();'
```

`locks` reports only waiting sessions and their real blocker relationships.
`stats` exposes cumulative read/hit/scan counters and numeric byte values;
optional cache data may be `null` when the capability is unavailable.
`bloat` starts with bounded estimates and performs bounded exact checks only
when requested (the default exact scan budget is 64 MiB).  Partial extension
capabilities and privilege failures remain closed, cumulative warnings rather
than hidden output.  JSON and TOON are equivalent projections of the same
typed document; progress and debug text never contaminates stdout.

`init-monitoring` is the only mutating diagnostic and requires `--yes`; its
dry-run plan is inert and never creates an extension.  Initialization is
available only for SDK-owned clusters and reports installed, already-present,
or explicitly skipped extensions.  External clusters reject mutation before
planning.  Native `psql` is deliberately different: it accepts only the
native passthrough arguments, keeps the bound host/user/database identity,
removes ambient `PGOPTIONS`, and inherits terminal streams, history behavior,
signals, and native exit status.  Do not pass document-format options to a
normal interactive `psql`; `--dry-run` is the plan-only exception and redacts
credentials.

To remove a database, use the CLI-private, cluster-bound operation:

```bash
odcli db rm feature_customer_credit --dry-run
odcli db rm feature_customer_credit --yes
odcli db rm feature_customer_credit --force-connections --yes
```

The dry-run checks the exact database name, protected `postgres`/`template0`/
`template1` names, template/default status, and active sessions without
connecting to Odoo or mutating the catalogue.  A normal Rich invocation asks
for confirmation; `--format json` and `--format toon` require `--yes` and never
prompt.
`--force-connections` terminates sessions belonging only to the exact target.
Remote instances, configured defaults, and template databases remain refused.

`backup rm`, `db rm`, and `env rm` accept variadic multi-target arguments.
Each target is resolved and previewed independently before any mutation; a
dry-run lists every target's plan. Any guarded failure during resolution or
preflight aborts before mutation; once execution starts, prepared targets are
attempted in order, failures are reported in the ordered aggregate result, and
the command exits nonzero without silently skipping targets or claiming rollback:

```bash
odcli db rm feature_a feature_b --dry-run
odcli backup rm UUID1 UUID2 UUID3 --yes
odcli env rm PROJ-123 PROJ-456 --dry-run
odcli env rm PROJ-123 --force-connections --yes
```

`env rm --force-connections` terminates sessions on the exact COPY database
being removed and routes through the same guarded PostgreSQL drop path as
`db rm --force-connections`. Without the flag, COPY removal stays fail-closed
and the error names `--force-connections`; `odcli stop` remains the
non-terminating alternative.

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
git_branch = "main"
```

`test_instance.database` is optional. When it is absent, remote refresh
preflight selects exactly one remote database through the existing HTTP list;
zero, many, or unavailable lists fail before download with distinct actionable
errors. An explicit `database` in the manifest or on `init --test-database`
takes priority and does not require list availability. The auto-detected name
appears in plan/result and backup provenance but is not written back to
`project.toml`.

```bash
odcli --env feature/customer-credit db refresh
ODCLI_ADMIN_PASSWORD='from-secret-store' odcli --env feature/customer-credit db reset-admin-password
```

Refresh follows the environment's configured database policy. Destructive
database operations are local-only and validate provenance before mutation.

### Backups, restores, and retained-resource diagnosis

Backup point commands use the exact full UUID and do not require an Odoo
worktree context. List or inspect retained records before a mutation:

```bash
odcli backup ls --format toon
odcli backup inspect 01234567-89ab-cdef-0123-456789abcdef --format json
odcli backup validate 01234567-89ab-cdef-0123-456789abcdef --format json
odcli backup rm 01234567-89ab-cdef-0123-456789abcdef --dry-run --format json
```

Restore previews are immutable. Rich mode confirms only after all preflight
checks; machine formats require `--yes` and never prompt. Long restores in
Rich mode publish real stages — backup preparation, auxiliary Odoo startup,
database restore, created-database verification, filestore restore, requested
administrator reset, and project default switch — with elapsed time and a
heartbeat during blocking actions without child stdout. Percent is shown only
when streaming dump or filestore bytes provide a trustworthy total. On error,
the safe primary cause and the last stage id are preserved. JSON and TOON keep
the existing single-document contract; terminal progress does not pollute
machine stdout. A restore switches the project default only after the database,
postcondition, audit, and optional administrator reset succeed. On interruption
or a later failure, the backup and any already-confirmed database are retained;
failure documents include sanitized UUID/target/state context and Ctrl-C exits
with `130`.

The read-only resource projections inspect retained backups, databases,
environments, logs, filestores, and owned volumes without reconciliation or
deletion:

```bash
odcli resource ls
odcli resource ls --format json
odcli resource doctor --format toon
```

Logical database bytes are not host-reclamation claims. Unknown ownership,
unavailable probes, crash-left `.part` files, and cleanup failures remain
visible with sanitized paths and recommendations. `resource ls` and
`resource doctor` never add, repair, delete, or reclassify lifecycle state.

### Storage migration and absolute paths

Global SDK state lives below `~/.odcli/`: configuration, the SQLite catalogue,
environments, projects, backups, locks, and pgAdmin data share this root. The
catalogue schema is managed by Alembic migrations; the first
catalogue-backed operation runs one locked, journaled migration from legacy
platformdirs locations and stamps the schema revision. A conflicting
destination or interrupted stage keeps the source data and journal so the next
invocation can report the exact problem and retry safely; sources are removed
only after verification. The repository-local `.odcli/` project manifest and
generated config are not part of this migration.

Global storage paths and machine-readable output retain absolute paths. Human
Rich tables may shorten paths beneath `HOME`, but `odcli env path` always emits
the absolute worktree path for safe shell navigation. Do not copy a displayed
`~` shorthand into JSON or use a project-local `.odcli/` path as global state.

### Module, translation, and Git workflows

Module discovery reads safe literal `__manifest__.py` files on demand; it does
not execute manifests or maintain a second module index. The first configured
add-ons root wins, shadowed modules are reported, dependencies are ordered
deterministically, and changed-module updates reuse the Git diff snapshot:

```bash
odcli module info sale
odcli module where sale
odcli module deps sale
odcli module install-order sale
odcli module update --changed --dry-run
```

Translation export remains a separate operation. It optionally captures the
absolute `msgfmt` executable with generated PO input and `LC_ALL=C`; missing
`msgfmt` is a typed unavailable result, not a Python dependency or an implicit
installer. Dry-run is inert and publication remains atomic:

```bash
odcli translations export --module sale --language en_US --dry-run --format json
```

Git operations are staged-only where applicable and use the shared immutable
plan boundary. `git commit` generates a tracker-neutral `[TAG] module:`
message, `git check` validates history and protected-branch rules, `git absorb`
is an optional captured `git-absorb` capability, and `git sync` fetches and
rebases before an optional same-name SSH publication. Use `--dry-run` before
mutation; a stale remote lease or conflict fails without an automatic retry:

```bash
odcli git commit "describe the staged change" --ticket PROJ-123 --dry-run
odcli git check --base main --format json
odcli git absorb --dry-run
odcli git sync --base main --push --dry-run
```

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
odcli --env feature/customer-credit run -- --dev=reload --log-level debug
odcli --env feature/customer-credit run --dry-run -- --stop-after-init -u sale
odcli --env feature/customer-credit shell --dry-run --format toon -- --dev
```

`--format json` and `--format toon` serialize the same captured plan in their
respective machine-readable forms. Supplying either output format without
`--dry-run` is rejected by Click with exit `2` before SDK resolution or process
launch. `logs
--follow`, the monitor server, and normal interactive shell/run streams are
documented native transports because they are intentionally unbounded or
interactive rather than finite plan documents.

`run -d` / `--detach` launches Odoo in the background through the same
inspect-then-run boundary, then returns once the process is alive with its
pid, endpoint, and log path. One shared resolver chooses the effective
logfile: an explicit non-empty `logfile` in effective `odoo.conf` wins;
otherwise `odoo.log` next to the effective config is created before spawn and
passed to Odoo through `--logfile` without editing the user's config. New
isolated environments write an environment-owned `<environment-root>/odoo.log`
into generated `odoo.conf`. `run --detach`, `logs`, structured results, and
runtime metadata use the same resolved path. `--dry-run` shows the path without
creating the directory or file; an unwritable fallback fails before spawn with
`logfile_unwritable` and the exact path. It is a bounded leaf: `--dry-run`
captures the detached plan without spawning, and the SDK sibling is
`instance.run_detached_command()` returning a typed `DetachedLaunchResult`:

```bash
odcli --env feature/customer-credit run -d --dry-run
odcli --env feature/customer-credit run -d -- --dev=reload
```

The literal `--` delimiter is required for every non-empty native Odoo argv;
the tokens after it are preserved in order and repeated values are allowed.
The SDK rejects managed runtime overrides before capture, including config and
database/credential, addons/upgrade/data-path, HTTP/gevent/longpolling bind or
port, and logfile option families. Dry-run captures and redacts the same argv
without recording use or starting Odoo; normal `run` forwards native stdin,
stdout, and stderr and returns Odoo's exit code.

### Bug reports and self-upgrade

`odcli bug-report init` creates a local draft under
`~/.odcli/bug-reports/<REPORT_ID>/` with `report.md`, `metadata.json`, and
`reviews/`. It works offline from any cwd. `odcli bug-report submit` validates
structure, size (262144-byte `report.md` cap), and secret redaction; publish
requires the last `reviews/N.json` (`N` in 1..3) to be `approved` for the
payload hash. There is no `--skip-review` or `--force`. `gh` is invoked through
`internal/proc` with `--body-file -`; the default label is `bug` only.
Use the portable `odcli-bug-report` agent skill for reviewer rounds and
emergency unblock paths.

`odcli update` safely upgrades a uv-tool install: default `--ref` is `main`,
`--check` resolves without mutation, and `--dry-run` may launch only the
read-only `uv` resolver/preflight needed to freeze the immutable target; it
never launches install or maintenance `odcli` steps. Exact-SHA ancestry probes
are captured Git process steps in that preflight; their ephemeral object-store
setup and mutating cleanup action are explicit, local VCS origins are verified
through the captured read-only preflight, and no unvalidated provenance URL
reaches `git fetch`. Downgrades fail closed unless the snapshot can restore
the complete current state.
Mutating execution uses an exclusive lock on
`~/.odcli/locks/odcli-update.lock`, snapshots affected metadata, installs
through `uv tool install --force`, and runs maintenance migrations in a child
`odcli update --format json` with `ODCLI_MAINTENANCE=1`. Pip, pipx, system, and
editable installs are refused.

```bash
odcli bug-report init --title "stop does not stop foreground run" --kind bug --dry-run
odcli bug-report submit 00000000-0000-0000-0000-000000000014 --dry-run
odcli update --check
odcli update --dry-run
```

`eval` and `exec` are finite shell-boundary operations and can use Rich, JSON,
or TOON output.  Their successful document keeps the expression/script result
separate from captured `user_stdout`; print-only execution reports a `null`
result.  A framed user-code exception exits `1` with a failure envelope whose
`error.details` retains the bounded output, structured exception/source
context, and truncation flag.  Startup failures use a command-specific
startup code and do not invent user-code details.  All result, output, source,
and diagnostic fields are secret-redacted.

## Complete CLI command reference

This is the complete shipped command-path inventory. The Click object
`odoo_instance_sdk.cli:cli` is its source of truth. Every entry has one purpose
sentence; use the entry's `--help` for exact options.

<!-- cli-command-inventory:start -->
- `odcli init` — Create or update the project manifest from explicit inputs.
- `odcli doctor` — Diagnose the resolved project, runtime, and PostgreSQL setup.
- `odcli bug-report init` — Create a local bug-report draft from explicit metadata.
- `odcli bug-report submit` — Validate and publish one local bug-report draft.
- `odcli env show` — Show one environment's selected runtime and ownership metadata.
- `odcli resource ls` — List retained local resources without lifecycle mutation.
- `odcli resource doctor` — Diagnose retained local resource findings without deletion.
- `odcli env create` — Plan or create an isolated Ticket worktree and environment (`env checkout` is the retained spelling).
- `odcli env ls` — List registered environments, active-only unless `--all` is requested.
- `odcli env path` — Print one active environment's absolute worktree path.
- `odcli env rm` — Remove a registered environment and its owned artifacts safely.
- `odcli env sync` — Rebuild or synchronize an environment's Python dependencies.
- `odcli stop` — Stop the selected project- or environment-owned proven runtime.
- `odcli backup ls` — List retained backup records with state and file presence.
- `odcli backup inspect` — Show one exact backup UUID with history and relationships.
- `odcli backup validate` — Validate one exact backup and report invalid versus unavailable.
- `odcli backup rm` — Preview, confirm, and delete one exact retained backup UUID.
- `odcli run` — Start resolved Odoo in the foreground from a project or environment.
- `odcli logs` — Read retained Odoo logs, optionally following new output.
- `odcli shell` — Open an interactive Odoo shell in the selected environment.
- `odcli eval` — Evaluate one Python expression through the Odoo shell boundary.
- `odcli exec` — Execute a Python script through the Odoo shell boundary.
- `odcli test` — Select and run Odoo tests, including changed-add-on selection.
- `odcli module ls` — Discover installable modules visible to the environment.
- `odcli module info` — Show one safely discovered module and manifest metadata.
- `odcli module where` — Show the resolved absolute path for one module.
- `odcli module deps` — Show direct dependencies and missing manifests.
- `odcli module install-order` — Plan a stable dependency installation order.
- `odcli module test` — Run tests for explicitly named modules.
- `odcli module update` — Upgrade explicitly named modules in the selected database.
- `odcli git commit` — Create one staged Odoo commit with a checked message.
- `odcli git check` — Validate Odoo commit history and branch safety.
- `odcli git absorb` — Absorb staged hunks with optional `git-absorb`.
- `odcli git sync` — Fetch, rebase, validate, and optionally publish a branch.
- `odcli translations export` — Export translations for a selected module and languages.
- `odcli deps verify` — Verify Python and add-on dependency readiness.
- `odcli vscode generate` — Generate VS Code launch configuration from project settings.
- `odcli postgres approve-image` — Pin trust to the resolved PostgreSQL image digest.
- `odcli postgres ps` — Report the configured PostgreSQL cluster state and endpoint.
- `odcli postgres up` — Start or verify the configured PostgreSQL cluster.
- `odcli postgres stop` — Stop an SDK-owned PostgreSQL cluster without deleting its volume.
- `odcli psql` — Run native `psql` with inherited terminal streams and bound identity.
- `odcli db locks` — Show bounded waiting-session blocker relationships.
- `odcli db stats` — Show bounded table/index statistics and cumulative counters.
- `odcli db bloat` — Show estimated bloat and optional bounded exact measurements.
- `odcli db init-monitoring` — Idempotently initialize supported monitoring extensions on an owned cluster.
- `odcli db refresh` — Refresh an environment database from its configured source policy.
- `odcli db ls` — List databases from the bound PostgreSQL cluster and known provenance.
- `odcli db restore` — Restore one exact retained backup or local Odoo ZIP into a selected database target.
- `odcli db reset-admin-password` — Reset the Odoo administrator password in the selected database.
- `odcli db rm` — Safely remove one exact local cluster database after guarded checks.
- `odcli ps` — Show process and resource inventory from one monitor snapshot.
- `odcli monitor` — Serve local environment snapshots in headless or dashboard mode.
- `odcli update` — Self-upgrade an OdCLI uv-tool install.
<!-- cli-command-inventory:end -->

Executable help is the source of truth for flags and retained aliases:

```bash
odcli --help
odcli env create --help
odcli module --help
odcli git --help
```

Click also supplies shell completion from the same command tree. Generate the
script with the shell-specific completion mode and install it using that
shell's normal completion directory, for example:

```bash
_ODCLI_COMPLETE=zsh_source odcli
_ODCLI_COMPLETE=bash_source odcli
_ODCLI_COMPLETE=fish_source odcli
```

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

Native runtime arguments are available through the same SDK instance. Pass a
tuple (or another sequence) to freeze the exact argv in the captured command;
the command's plan and execution use that same snapshot:

```python
command = instance.run_foreground_command(
    args=("--dev=reload", "--log-level", "debug", "--stop-after-init")
)
print(command.plan)       # redacted argv, including native arguments
result = command.run()    # native inherited stdin/stdout/stderr
print(result)
```

SDK foreground calls reject the managed config and database/credential,
addons/upgrade/data-path, HTTP/gevent/longpolling bind or port, and logfile
families. Other Odoo arguments may repeat and retain their original boundaries
and order. The CLI equivalent requires the literal `--` delimiter; its
`--dry-run` preview does not record use or execute the command.

See [Python SDK examples](docs/python-sdk.md) for runnable examples covering
database backup/restore, catalogue inspect, database inventory, dependency
verification, shared test execution, persisted environment stop, COPY database
replacement, environments, PostgreSQL, monitoring, and inspect-then-run
command siblings. `PUBLIC_LEAF_CASES` records the SDK primitive or CLI-only
reason for every leaf; the complete boundary inventory and allowlist rationale
are in [docs/execution-boundary.md](docs/execution-boundary.md).

The SDK-first rule governs that boundary: every CLI leaf records either a
public `sdk_primitive` or a concrete `cli_only_reason` for transport-only
leaves such as `run`, `shell`, `logs --follow`, and `monitor`. CLI callbacks
must not build self-contained domain read/mutation/spawn operations through
`internal.*` when a public typed SDK primitive applies; convenience methods
delegate to the corresponding `*_command()` sibling and do not rebuild argv,
cwd, environment, or actions. Production Odoo HTTP calls are centralized in
the internal `OdooHttpClient` transport layer (`internal/transport/`); it is
not a public SDK type and `httpx` remains absent after `import odoo_instance_sdk.cli`.
XML-RPC stays in test support only; `src/` contains zero `ServerProxy` call sites.

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

The default environment inventory is active-only. Registered projects are also
visible when they have no environment; a project-owned runtime is reported
directly on that project rather than through a synthetic environment.  A
missing runtime is `null`, while a recorded stopped runtime retains its typed
metrics with null/empty live values. Use explicit include-removed options where
supported. Monitor snapshots isolate component failures and never publish
stored secrets or absolute catalog paths.

## Security and data location

Secrets are never written to the project manifest. Generated secret config and
PostgreSQL credentials use restricted user-data files; the backup catalog uses
the canonical `~/.odcli/` root. Remote destructive database operations are
rejected. Repository-selected remote test instances require an exact external
origin pin. Pinned HTTP origins are permitted for legacy deployments, but emit
a warning because the master password crosses the network in cleartext; HTTPS
remains strongly recommended.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and verification, the
[changelog](CHANGELOG.md) for shipped behavior, and
[GitHub Issues](https://github.com/maximchikAlexandr/odoo-instance-sdk/issues)
for defects and roadmap proposals. Roadmap issues describe planned work, not
features shipped by the current package.

## License

[MIT](LICENSE)
