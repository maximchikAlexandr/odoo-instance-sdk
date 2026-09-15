## ADDED Requirements

### Requirement: `odcli ps` leaf registration

The CLI SHALL register `odcli ps` as a bounded structured leaf accepting `--all-projects`, `--watch`, `--interval SECONDS`, `--format rich|json|toon`, and `--fields`. It SHALL delegate the domain operation to `EnvironmentMonitor.processes_command()` and SHALL NOT build a parallel process collector. The leaf SHALL follow the shared output contract, exit codes, and redaction rules. It SHALL NOT appear as a second top-level process command alongside another `odcli top`.

#### Scenario: Help lists ps

- **WHEN** `odcli --help` runs
- **THEN** the command surface includes `ps`

#### Scenario: ps delegates to the SDK

- **WHEN** `odcli ps --format json` runs
- **THEN** the envelope wraps the result of `EnvironmentMonitor.processes_command()` and the CLI performs no second collection

### Requirement: VS Code profile carries project default_run_args

`odcli vscode generate` SHALL reuse the same resolved runtime/argv source as `odcli run`. Safe `default_run_args` from the resolved project manifest SHALL appear in the generated launch profile `args` exactly once. An empty `default_run_args` list SHALL add no arguments. Validation of disallowed managed-override families (config, database/credential, addons/upgrade/data-path, HTTP/gevent/longpolling bind or port, and logfile) SHALL be preserved for both project and environment contexts. The profile SHALL NOT include automatic module update/install arguments or secrets.

#### Scenario: Project default_run_args appear in profile

- **WHEN** `odcli vscode generate` runs from a project with `default_run_args = ["--dev=qweb,xml"]`
- **THEN** the generated profile `args` contain `--dev=qweb,xml` exactly once

#### Scenario: Empty default_run_args add nothing

- **WHEN** `odcli vscode generate` runs from a project with an empty `default_run_args` list
- **THEN** the generated profile `args` contain no extra arguments beyond the required runtime arguments

#### Scenario: Disallowed overrides are rejected

- **WHEN** `default_run_args` contains a managed-override family such as `--config`
- **THEN** the command fails before profile generation with a sanitized actionable error

### Requirement: Checkout inventory for env list

`odcli env list` SHALL project one frozen `CheckoutInventory` model for Rich, JSON, and TOON. The main checkout of each selected project SHALL appear as the first typed row of its group with `kind = main | environment`, a stable `project_id`, and a nullable `environment_id`. The main checkout SHALL NOT be modelled as a synthetic environment.

The base inventory row SHALL contain only working identity and state: kind/name and project; branch, short SHA, and canonical worktree path; commits ahead of the base branch plus added and deleted lines; a compact Odoo status `running | stopped | unavailable` without PID or metrics; and the bound database/DB mode when applicable.

Rich SHALL NOT show `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, `SIZE`, or detailed process/artifact columns; those values live in `odcli ps`. Rich SHALL remain a readable `Table` with headers and checkout rows on both normal and compact terminal widths and SHALL NOT replace the table with `branch=... state=...` blocks. The same table contract SHALL hold under `--watch`.

`CheckoutInventory` SHALL be built from one canonical `EnvironmentMonitor.snapshot()` per sample plus Git facts of the main checkout. A separate monitor or collector SHALL NOT be added. The raw `EnvironmentMonitor.snapshot()` SHALL remain the canonical source for `odcli ps`, the Python SDK, FastAPI, and the dashboard.

#### Scenario: Main checkout is the first row

- **WHEN** `odcli env list` runs inside a project with one environment
- **THEN** the first row of that project's group is the main checkout with `kind=main` and no synthetic environment is created

#### Scenario: Rich drops process columns

- **WHEN** `odcli env list` renders a Rich table
- **THEN** the columns `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, and `SIZE` are absent

#### Scenario: Stopped checkout stays visible

- **WHEN** `odcli env list` runs and the main checkout's Odoo is stopped
- **THEN** the main checkout row remains visible with `running | stopped | unavailable` status and no PID

#### Scenario: One frozen model across formats

- **WHEN** `odcli env list --format json` and `odcli env list --format toon` run
- **THEN** both wrap the same `CheckoutInventory` model, not three different field sets

### Requirement: Environment facts entry point

One narrow environment-facts entry point SHALL allow `odcli-codex` (#68), `odcli-openspec` (#69), and `odcli-multica` (#70) to attach read-only facts to `CheckoutInventory` rows. The entry point SHALL be a single explicitly typed callable/protocol discovered via Python entry points. A provider SHALL receive immutable core checkout rows and return frozen summaries; it SHALL NOT patch Click or Rich, run its own live loop, or mutate the core snapshot.

Each installed provider SHALL add at most one compact Rich column. JSON/TOON SHALL store summaries nested under a stable provider ID without dynamic top-level fields. The minimal summary SHALL contain `provider`, `state`, `text`, and concrete typed details; `Any`, `object`, and Rich renderables SHALL NOT be used. Collection SHALL have deterministic provider order, a short bounded timeout, and isolated per-provider/per-row errors. A missing optional package SHALL NOT add an empty column and SHALL NOT be an error. A failed, incompatible, or slow provider SHALL NOT hide core rows or other integrations and SHALL NOT write progress or noise to machine stdout.

This entry point SHALL NOT become a general lifecycle or provider framework; it serves only read-only facts for `CheckoutInventory`. Process groups continue to use the separate contract from the `process-inventory` capability.

#### Scenario: One provider adds one column

- **WHEN** exactly one environment-facts provider is installed
- **THEN** Rich `env list` shows at most one extra compact column from that provider

#### Scenario: Missing provider is not an error

- **WHEN** no environment-facts provider is installed
- **THEN** `env list` renders core rows with no extra column and no error

#### Scenario: Failed provider does not hide rows

- **WHEN** one environment-facts provider raises an error
- **THEN** core rows and other providers' summaries remain visible and machine stdout is uncontaminated

### Requirement: Safe multi-target deletion

`odcli backup rm`, `odcli db rm`, and `odcli env rm` SHALL accept variadic positional arguments for their respective target types and SHALL reuse the existing single-target resolvers, validators, and command builders. A generic bulk SDK, parallel deletion, or new orchestration hierarchy SHALL NOT be added.

Before the first destructive action, the command SHALL resolve all targets and perform available planning preflight. An unknown, ambiguous, out-of-scope, or duplicate target SHALL abort the command with no changes. After successful preflight, Rich SHALL show one confirmation listing all sanitized targets; machine modes without `--yes` SHALL change nothing and SHALL emit `confirmation_required`.

Execution SHALL run single-target commands sequentially in argument order. Immediately before each deletion, the existing execution-time revalidation SHALL run. A per-target execution-time failure SHALL continue remaining prepared targets, return per-target success/failure, and produce a non-zero exit code on partial failure. `--dry-run` SHALL return one ordered aggregate plan with each target's plan and SHALL perform no destructive action. Rich SHALL show a compact per-target summary; JSON/TOON SHALL return one document with ordered per-target results/errors, not several glued envelopes.

`backup rm` SHALL accept multiple full UUIDs, including UUIDs from different projects and unowned backups, preserving existing catalog/file-state and binding checks. `db rm` SHALL accept multiple exact database names only within one resolved project cluster; cross-project database deletion, `PROJECT:DATABASE`, a new database UUID, and global name search SHALL NOT be introduced. `db rm` and copy-environment cleanup SHALL NOT start Odoo and SHALL use the existing guarded PostgreSQL path. `env rm` SHALL accept multiple full UUIDs/selectors, resolving each target's persisted repository, Git common dir, worktree, and PostgreSQL cluster independently. `env rm` without a positional argument SHALL preserve the existing cwd semantics for exactly one environment. Shared environment cleanup SHALL NOT drop the database; copy environment cleanup SHALL preserve the full database/backup/worktree cleanup contract. `--force-default` and `--force-connections` SHALL apply to the whole set but SHALL be checked per database.

Single-target calls SHALL remain backward-compatible in safety semantics, confirmation, dry-run, and exit status.

#### Scenario: Backup rm deletes multiple UUIDs

- **WHEN** `odcli backup rm UUID1 UUID2 --yes` runs with two valid backups from different projects
- **THEN** both are deleted and one document with ordered per-target results is returned

#### Scenario: Db rm is cluster-scoped

- **WHEN** `odcli db rm db1 db2 --yes` runs inside one resolved project cluster
- **THEN** only those names in that cluster are considered and same-named databases in other clusters are untouched

#### Scenario: Env rm resolves each target independently

- **WHEN** `odcli env rm UUID1 UUID2 --yes` runs with UUIDs from different projects
- **THEN** each target's repository and cluster context is resolved separately and no cwd context is applied to the whole set

#### Scenario: Env rm without arguments preserves cwd semantics

- **WHEN** `odcli env rm` runs from inside an exact registered worktree
- **THEN** it resolves exactly that environment, preserving the existing single-target behavior

#### Scenario: Planning preflight aborts before mutation

- **WHEN** one target in a multi-target call is unknown
- **THEN** the command aborts with no changes and a sanitized error

#### Scenario: Partial failure is non-zero

- **WHEN** the second of three targets fails at execution time
- **THEN** the first and third targets are still attempted and the command exits non-zero with per-target results

### Requirement: Rich absolute-time formatting

All absolute date/time values shown to humans in Rich tables, details, or panels SHALL be converted to the local timezone via `datetime.astimezone()` and formatted as `YYYY-MM-DD HH:MM`. Seconds, microseconds, the `T` separator, `Z`, and UTC offsets SHALL NOT appear in Rich absolute-time fields. Naive timestamps from SQLite created via UTC `datetime('now')` SHALL be treated as UTC first, then converted to local timezone. Duration fields (`elapsed`, uptime, timeout, intervals) SHALL NOT pass through the absolute-time formatter.

JSON and TOON SHALL keep the full timezone-aware ISO timestamp and original precision without schema changes. One small helper in the existing internal formatting module SHALL be reused; no timezone dependency, locale/config option, or separate formatting layer SHALL be added.

#### Scenario: Backup ls shows local time

- **WHEN** `odcli backup ls` renders `Catalogue time`
- **THEN** the value is in local timezone formatted as `YYYY-MM-DD HH:MM`

#### Scenario: Backup inspect formats all time fields

- **WHEN** `odcli backup inspect` renders Rich
- **THEN** `catalogue_time`, `history.occurred_at`, and `restore_links.restored_at` are in local timezone formatted as `YYYY-MM-DD HH:MM`

#### Scenario: UTC with non-zero offset crosses day boundary

- **WHEN** a UTC timestamp near midnight is rendered with a non-zero local offset
- **THEN** the displayed date and time reflect the correct local date including day-boundary crossing

#### Scenario: Naive SQLite UTC is treated as UTC

- **WHEN** a naive SQLite `datetime('now')` value is rendered in Rich
- **THEN** it is interpreted as UTC and converted to local timezone before formatting

#### Scenario: JSON keeps ISO precision

- **WHEN** `odcli backup ls --format json` runs
- **THEN** the timestamp retains the full timezone-aware ISO value and original precision

#### Scenario: Durations are not reformatted

- **WHEN** a duration field such as `elapsed` is rendered in Rich
- **THEN** it is not passed through the absolute-time formatter

### Requirement: Detached Odoo launch

`odcli run` SHALL accept `-d, --detach` to launch Odoo detached. Without the flag, the existing foreground contract SHALL be preserved. Detached mode SHALL use the existing project/environment resolution, port preflight, PostgreSQL preflight, argv construction, process executor, runtime identity, and ownership/stop mechanism. A separate daemon manager or second command-construction path SHALL NOT be introduced.

After spawn, the command SHALL confirm the process is alive and runtime identity is persisted, then return PID, project/environment identity, HTTP endpoint, and log path without waiting for Odoo to finish. If the process exits immediately, the command SHALL return an error and SHALL NOT leave a false `running` record. The Odoo lifetime SHALL NOT be tied to the terminal or CLI: exiting `odcli run -d` SHALL NOT terminate the child. Stopping SHALL work through the existing `odcli stop` and stale runtime records SHALL be recognized normally.

Logs SHALL be written to the existing `logfile` of the bound `odoo.conf`. When no logfile is configured, the command SHALL fail fast before spawn with a clear diagnostic; stdout/stderr SHALL NOT be lost and a parallel log store SHALL NOT be created. `odcli logs --tail` and `odcli logs --follow` SHALL work after detached launch. `--dry-run -d` SHALL show the exact sanitized Odoo process command and detached lifecycle plan without spawning. Rich SHALL give a short status; JSON/TOON SHALL return one typed result. Incompatible combinations with native Odoo args after `--` and output formats SHALL be rejected by existing Click validation and `-d` after the literal `--` SHALL be treated as a native Odoo argument.

#### Scenario: Detached run returns promptly

- **WHEN** `odcli run -d` launches successfully
- **THEN** the command returns promptly with PID, identity, endpoint, and log path while Odoo continues running

#### Scenario: Immediate exit is not success

- **WHEN** the detached Odoo process exits immediately after spawn
- **THEN** the command returns an error and leaves no stale active runtime record

#### Scenario: Stop targets the detached runtime

- **WHEN** `odcli stop` runs after a detached launch
- **THEN** it stops exactly that persisted runtime

#### Scenario: Logs work after detached launch

- **WHEN** `odcli logs --tail` and `odcli logs --follow` run after a detached launch
- **THEN** both read from the bound logfile and follow produces new lines

#### Scenario: Detached dry-run does not spawn

- **WHEN** `odcli run --dry-run -d` runs
- **THEN** the sanitized process command and detached lifecycle plan are shown and no process or runtime record is created

#### Scenario: Foreground behavior is unchanged

- **WHEN** `odcli run` runs without `-d`
- **THEN** signals, exit code, stdio, and cleanup match the existing foreground contract

### Requirement: SDK-first CLI leaf contract

Every entry in the canonical `PUBLIC_LEAF_CASES` SHALL carry exactly one of: an `sdk_primitive` referencing the public typed SDK call the CLI delegates to, or a `cli_only_reason` with a concrete transport/presentation reason why the operation stays CLI-only. A generic formulation such as "convenient for CLI" SHALL NOT be accepted. A new CLI leaf SHALL NOT pass contract tests without one of these two values.

CLI callbacks SHALL NOT build a self-contained domain read/mutation/spawn operation through `internal.*` when a public typed SDK primitive applies. The SDK SHALL own typed inputs/results, `Command`, immutable plans, revalidation, process/actions, cleanup, and failure semantics. Convenience methods SHALL delegate to the corresponding `*_command()` sibling and SHALL NOT rebuild the snapshot. The public SDK SHALL NOT export Click context, Rich renderables, CLI envelopes, or private executor callbacks.

#### Scenario: Every leaf has a primitive or reason

- **WHEN** the `PUBLIC_LEAF_CASES` contract test runs
- **THEN** every entry has either a non-empty `sdk_primitive` or a concrete `cli_only_reason`

#### Scenario: New leaf without primitive or reason is rejected

- **WHEN** a new CLI leaf is added without `sdk_primitive` or `cli_only_reason`
- **THEN** the contract test fails

#### Scenario: CLI-only reason is concrete

- **WHEN** a `cli_only_reason` is inspected
- **THEN** it names a specific transport or presentation boundary, not a general convenience statement

## MODIFIED Requirements

### Requirement: `odcli run`

`odcli run` SHALL launch the resolved Odoo runtime from either a ready environment or an initialized project. For project context, it SHALL derive the Python executable, Odoo entry point, source Odoo config, runtime working directory, preferred HTTP port, default database, default run arguments, and project PostgreSQL binding from `.odcli/project.toml` and the referenced config. Missing required runtime fields or files SHALL fail before process construction with a sanitized actionable error.

The command SHALL preserve the existing literal `--` delimiter rule, exact passthrough argument order, protected runtime-identity validation, free-port preflight, dry-run rendering, inherited native streams, foreground process-group cleanup, and exit-code behavior. Project context has no environment use metadata, so it SHALL NOT call `EnvironmentResource.record_use()`; environment context SHALL retain its existing record-use behavior after successful preflight and before execution.

The command SHALL accept `-d, --detach` to launch Odoo detached as specified by the detached Odoo launch requirement. Foreground behavior without `-d` SHALL remain unchanged. `-d` after the literal `--` SHALL be treated as a native Odoo argument and SHALL NOT be interpreted as an OdCLI option.

#### Scenario: Project run needs no runtime path arguments

- **WHEN** `odcli run` executes from an initialized main checkout whose manifest references valid Python, Odoo entry point, and config
- **THEN** it constructs and launches the foreground command without requiring those paths as CLI arguments

#### Scenario: Project defaults and passthrough compose deterministically

- **WHEN** project context defines default run arguments and the caller supplies allowed arguments after `--`
- **THEN** the captured command contains project defaults followed by the exact caller arguments in their original order

#### Scenario: Project dry-run has no side effects

- **WHEN** `odcli run --dry-run` resolves project context
- **THEN** it emits the bounded execution plan without starting Odoo, mutating the environment catalogue, or writing use metadata

#### Scenario: Environment run retains metadata behavior

- **WHEN** `odcli run` resolves a ready environment and the port preflight succeeds
- **THEN** it records environment use exactly once before executing the captured foreground command

#### Scenario: Native process contract is context-independent

- **WHEN** an environment-based or project-based foreground run exits non-zero or is interrupted
- **THEN** native streams are preserved, the actual exit code is returned, and interrupt cleanup returns exit `130`

#### Scenario: Port conflict deterministic error

- **WHEN** `odcli run -- --dev=reload` finds the effective bound port occupied
- **THEN** it returns `port-conflict` with ownership unknown and performs no foreground command construction, use update, config change, or process launch

#### Scenario: Free port starts Odoo

- **WHEN** `odcli run -- --dev=reload --log-level debug --dev=xml` finds the effective port free
- **THEN** it captures `run_foreground_command` once with the exact delimiter arguments and executes that captured command

#### Scenario: Delimiter is required for native arguments

- **WHEN** a caller invokes `odcli run --dev=reload` without the `--` delimiter
- **THEN** Click reports an unknown-option usage error with exit code `2` before SDK resolution or launch

#### Scenario: Bare positional input is rejected

- **WHEN** a caller invokes `odcli run sale` without a literal `--`
- **THEN** the command reports a usage error with exit code `2` and performs no SDK resolution, use update, command construction, or launch

#### Scenario: Protected override is rejected before spawn

- **WHEN** `odcli run -- --database other` or another protected runtime-identity override is invoked
- **THEN** the SDK validator returns a sanitized error before command execution and no child process starts

#### Scenario: Dry-run and execution use one captured argv

- **WHEN** the same allowed delimiter arguments are supplied to dry-run and normal execution under a recording executor
- **THEN** dry-run displays the exact captured foreground step and normal execution consumes it without reconstructing argv

#### Scenario: Native TTY and exit behavior remain unchanged

- **WHEN** `odcli run -- --workers=2` executes normally, exits non-zero, or is interrupted
- **THEN** inherited stdin/stdout/stderr remain native, the real exit code is returned, and interrupt cleanup preserves exit `130`

#### Scenario: Detached flag after literal delimiter is native

- **WHEN** `odcli run -- -d` is invoked
- **THEN** `-d` is passed to Odoo as a native argument and is not interpreted as the detach option

### Requirement: `vscode generate`

```bash
odcli vscode generate
odcli vscode generate --write
```

The command SHALL accept the shared `environment | project` context. In project context it SHALL derive Python, Odoo entry point, source config, repository root, and configured database from the initialized project; in environment context it SHALL preserve existing generated-config and recorded-runtime behavior. The generated profile SHALL reuse the same resolved runtime/argv source as `odcli run`, including safe `default_run_args` from the resolved project manifest, which SHALL appear in `args` exactly once. An empty `default_run_args` list SHALL add no arguments. Validation of disallowed managed-override families SHALL be preserved. The profile SHALL NOT include automatic module update/install arguments or secrets. Default SHALL print one debugpy profile; `--write` SHALL atomically create a missing `.vscode/launch.json` and SHALL NOT rewrite existing JSONC. `--dry-run` SHALL report the intended write without creating directories or files. Rich, JSON, and TOON SHALL use the shared output contract.

#### Scenario: Print profile

- **WHEN** `odcli vscode generate` runs from a complete initialized project with no exact environment
- **THEN** one profile derived from project values is printed and no file is written

#### Scenario: Existing JSONC is preserved

- **WHEN** `--write` targets an existing `.vscode/launch.json`
- **THEN** the command refuses to overwrite it in either context

#### Scenario: Project default_run_args appear in profile

- **WHEN** `odcli vscode generate` runs from a project with `default_run_args = ["--dev=qweb,xml"]`
- **THEN** the generated profile `args` contain `--dev=qweb,xml` exactly once

#### Scenario: Empty default_run_args add nothing

- **WHEN** `odcli vscode generate` runs from a project with an empty `default_run_args` list
- **THEN** the generated profile `args` contain no extra arguments beyond the required runtime arguments

### Requirement: `odcli env list`

```bash
odcli env list [--format rich|json|toon]
odcli env list --all [--format rich|json|toon]
odcli env list --all-projects [--format rich|json|toon]
odcli env list --watch [--interval SECONDS]
```

The command SHALL project one frozen `CheckoutInventory` model for Rich, JSON, and TOON. It SHALL invoke `EnvironmentMonitor.snapshot()` exactly once per one-shot rendering and once per live refresh as the canonical source, then build `CheckoutInventory` from that snapshot plus Git facts of the main checkout. It SHALL NOT instantiate `OdooClient` to read backups/environments, query `BackupCatalog`, run Git/Docker/filesystem reconciliation, probe ports, regroup catalog rows into an alternative model, or perform any other collection after the snapshot returns.

The main checkout of each selected project SHALL appear as the first typed row of its group with `kind = main | environment`, a stable `project_id`, and a nullable `environment_id`. The main checkout SHALL NOT be modelled as a synthetic environment. The base row SHALL contain only working identity and state: kind/name and project; branch, short SHA, and canonical worktree path; commits ahead of the base branch plus added and deleted lines; a compact Odoo status `running | stopped | unavailable` without PID or metrics; and the bound database/DB mode when applicable.

Rich SHALL NOT show `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, `SIZE`, or detailed process/artifact columns; those values live in `odcli ps`. Rich SHALL remain a readable `Table` with headers and checkout rows on both normal and compact terminal widths and SHALL NOT replace the table with `branch=... state=...` blocks. The same table contract SHALL hold under `--watch`. Rich color/ANSI SHALL be enabled only when supported by the output terminal.

`--all-projects` and project-context behavior SHALL remain unchanged. `--all` SHALL request `include_removed=True` only for Rich output so the existing observable contract remains: Rich includes removed rows, while JSON/TOON wrap the default non-removed `CheckoutInventory`. JSON and TOON SHALL use `command="env.list"` and project the same frozen `CheckoutInventory` as Rich; TOON differs only in serialization. The raw `EnvironmentMonitor.snapshot()` SHALL remain the canonical source for `odcli ps`, the Python SDK, FastAPI, and the dashboard. A separate monitor or collector SHALL NOT be added.

#### Scenario: Main checkout is the first row

- **WHEN** `odcli env list` runs inside a project with one environment
- **THEN** the first row of that project's group is the main checkout with `kind=main` and no synthetic environment is created

#### Scenario: Rich drops process columns

- **WHEN** `odcli env list` renders a Rich table
- **THEN** the columns `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, and `SIZE` are absent

#### Scenario: One frozen model across formats

- **WHEN** `odcli env list --format json` and `odcli env list --format toon` run
- **THEN** both wrap the same `CheckoutInventory` model, not three different field sets

#### Scenario: --all human includes removed, JSON does not

- **WHEN** `odcli env list --all` prints human table and `odcli env list --format json --all` emits JSON
- **THEN** human table includes `STATE=removed` rows; JSON `CheckoutInventory` rows contain only non-removed checkouts

#### Scenario: Stopped checkout stays visible

- **WHEN** `odcli env list` runs and the main checkout's Odoo is stopped
- **THEN** the main checkout row remains visible with `running | stopped | unavailable` status and no PID

#### Scenario: Watch uses one snapshot per sample

- **WHEN** `odcli env list --watch` refreshes
- **THEN** it calls `EnvironmentMonitor.snapshot()` once and builds `CheckoutInventory` from that single sample without a second collector

#### Scenario: Grouped by project with cluster header

- **WHEN** `env list` runs with two projects
- **THEN** output has two `Project <name>` headers each followed by a `PostgreSQL ...` cluster summary line, then that project's checkout rows

#### Scenario: JSON parity with monitor snapshot

- **WHEN** `odcli env list --format json --all-projects` runs
- **THEN** `result`/`data` payload is the frozen `CheckoutInventory` built from the same `EnvironmentMonitor.snapshot()` and `GET /api/v1/snapshot` remains the raw snapshot for `odcli ps`

#### Scenario: Grouped Rich table uses one result

- **WHEN** Rich `env list` runs with two projects
- **THEN** output has two project sections and all displayed fields originate from one `CheckoutInventory` built from one `EnvironmentMonitor.snapshot()` result

#### Scenario: JSON and TOON parity with monitor snapshot

- **WHEN** `odcli env list --format json --all-projects` and `--format toon --all-projects` render the same sample
- **THEN** decoded `result`/`data` equal the JSON-safe `CheckoutInventory` object with main checkout and environment rows, cluster summaries, and Git facts

#### Scenario: --all compatibility

- **WHEN** `odcli env list --all` renders Rich and `odcli env list --all --format json` or `--format toon` renders a machine document
- **THEN** Rich includes `lifecycle_state="removed"` rows while both machine documents contain only non-removed `CheckoutInventory` rows

#### Scenario: CLI does not recollect inventory

- **WHEN** the `env list` command and renderers are exercised with a supplied typed snapshot
- **THEN** no CLI code opens the catalog, lists backups/environments, calls Git or Docker, probes a port, or performs filesystem reconciliation