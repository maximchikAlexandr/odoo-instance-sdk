## ADDED Requirements

### Requirement: Lightweight CLI transport boundary

The CLI SHALL remain a Click inbound adapter with `odoo_instance_sdk.cli:cli` as the stable import and entry point. `cli.py` SHALL own registration and composition, while `commands/context.py`, `commands/output.py`, and `commands/env.py` SHALL own only the affected CLI context, output policy, and environment command adapter responsibilities.

On the affected canonical environment inventory path, `EnvironmentMonitor` and its reusable private collectors SHALL return typed operation results and SHALL NOT import Click or FastAPI, print output, inspect transport flags, or return CLI-envelope or React-shaped dictionaries. Existing result-building inside unrelated legacy `--json` callbacks MAY remain while those callbacks adopt the shared CLI envelope/emitter. CLI callbacks MAY parse syntax, resolve CLI context, select output mode, invoke public resources, render the result, and map SDK exceptions to exit codes. The change SHALL NOT add a DI container, command bus, handler registry, renderer interface/registry/DSL, generic application/service/provider layer, or a second implementation interface.

Unrelated command groups SHALL remain in their current modules unless the output helper must be reused; empty or symmetry-only modules SHALL NOT be created.

#### Scenario: Resource operation is transport independent

- **WHEN** `EnvironmentMonitor.snapshot()` is invoked from Python without the CLI
- **THEN** it returns the same typed inventory consumed by `env list` and imports neither Click nor FastAPI

#### Scenario: Stable Click entry point

- **WHEN** callers import `cli` from `odoo_instance_sdk.cli` or execute the installed `odcli` script
- **THEN** the same Click command tree is available through `odoo_instance_sdk.cli:cli`

#### Scenario: No speculative framework

- **WHEN** the CLI boundary implementation is inspected
- **THEN** it contains direct Click-to-resource composition and no generic registry, command bus, DI container, or single-implementation provider hierarchy

### Requirement: Typed CLI context on affected paths

The root Click callback SHALL create one small typed CLI context carrying the explicit project selector, explicit environment selector, resolved project/environment values when available, and their provenance. A native Click typed passing mechanism such as `make_pass_decorator` SHALL replace direct untyped `ctx.obj` dictionary access on every path touched by this change.

Reusable resolvers and workflows SHALL accept Python values and SHALL NOT accept `click.Context` or read transport flags. Existing project and environment resolution order and provenance values SHALL remain unchanged. Migrating an unrelated callback from `ctx.obj` is out of scope unless required to keep a shared resolver correct.

#### Scenario: Explicit project provenance survives typed context

- **WHEN** `odcli --project /path/to/repo env list --format json` resolves the project
- **THEN** the envelope reports `project_source="explicit"` without a callback reading a dictionary key from `ctx.obj`

#### Scenario: Resolver is Click-free

- **WHEN** a project or environment resolver is imported and called in a Python test
- **THEN** its signature contains no `click.Context` and it resolves from typed Python inputs

### Requirement: Live Rich environment inventory

`odcli env list` SHALL accept `--watch` and `--interval SECONDS`. `--watch` SHALL be valid only for `rich` mode when stdout is an interactive TTY. `--interval` SHALL default to `2.0` and SHALL reject values below `0.1` as a Click usage error with exit code `2`.

The live loop SHALL use `rich.live.Live` and repeatedly invoke the same `EnvironmentMonitor.snapshot(project_id=..., include_removed=...)` query used by the one-shot command. Every refresh SHALL retain the original project selection, `--all`, `--all-projects`, and deterministic project/environment ordering; this change SHALL NOT add a separate live query or a new sort option.

After at least one successful sample, a collection failure SHALL keep the last successful table visible, display a sanitized diagnostic in the live region or stderr, and retry at the selected interval. Failure of the initial sample SHALL exit `1`. The loop SHALL use `Live(..., transient=True)` or explicit equivalent cleanup. `Ctrl-C` SHALL stop polling, close the Live context, restore the terminal, remove the live region and last table, leave no task/thread/process behind, and exit `130`.

The live renderer SHALL use `Table` and `Live`; it SHALL use `Status` or `Progress` only for an operation with real measurable progress and SHALL NOT show a fabricated progress bar during snapshot polling.

#### Scenario: Watch refreshes the canonical query

- **WHEN** `odcli env list --watch --interval 2` runs in an interactive terminal
- **THEN** one Rich live table is refreshed from successive canonical inventory snapshots with the original filters and deterministic ordering

#### Scenario: Watch rejects machine output

- **WHEN** `odcli env list --watch --format json` or `--format toon` is invoked
- **THEN** Click exits `2` without starting a live loop or emitting a partial machine document

#### Scenario: Watch rejects non-interactive output

- **WHEN** `odcli env list --watch` is invoked with stdout redirected or captured
- **THEN** the command exits `1` with a sanitized diagnostic and leaves stdout free of a partial live display

#### Scenario: Later sample failure retains data

- **WHEN** a successful live sample is followed by a monitor failure
- **THEN** the last successful inventory remains displayed and the loop retries without replacing it with an empty graph

#### Scenario: Watch interrupt cleans up

- **WHEN** the user presses Ctrl-C during live refresh or interval waiting
- **THEN** Rich restores the terminal, removes the live region so the last table is not left in scrollback, the polling loop exits, no background work remains, and the command exits `130`

### Requirement: CLI compatibility characterization

Before move-only changes, automated characterization tests SHALL pin the root/subcommand help and command tree, exit codes `0`, `1`, `2`, and `130`, stdout/stderr routing, JSON success/error envelope v1, `--json` behavior, cwd/project/environment resolution, `env list --all`/`--all-projects`, redaction, public imports, and native streams for `run`, `shell`, and `logs --follow`.

Move-only and semantic output/watch changes SHALL be kept in separate commits on `feat/MYL-55-cli-output-boundary` so regressions can be attributed independently.

#### Scenario: Passthrough streams remain native

- **WHEN** characterization tests execute `run`, interactive `shell`, or `logs --follow`
- **THEN** their stdin/stdout/stderr and exit/interrupt behavior match the pre-change contract and no envelope or Rich live wrapper is introduced

#### Scenario: Help tree remains stable

- **WHEN** characterization tests compare root and subcommand help after the refactor
- **THEN** all existing command names and required options remain present, with only the specified additive format/watch options

## MODIFIED Requirements

### Requirement: Stable machine output

The exact bounded structured leaf inventory is: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `eval`, `exec`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Each SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Existing `--json` SHALL remain a backward-compatible alias for `--format json`. Supplying `--json` with `--format toon` or `--format rich` SHALL be a Click usage error with exit code `2`; supplying `--json --format json` SHALL be accepted. `run`, interactive `shell`, and `logs --follow` SHALL remain raw-streaming and SHALL accept neither document output nor a Rich live wrapper.

The CLI SHALL define one CLI-only `OutputMode` with values `rich`, `json`, and `toon`. The mode and envelope types SHALL NOT become public SDK models or FastAPI response models. Each successful or failed bounded operation SHALL first build one JSON-safe CLI envelope v1 containing `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, and `warnings`; success SHALL contain equal `result` and `data`, and failure SHALL contain stable `error.code` and sanitized `error.message`.

JSON and TOON SHALL serialize that exact envelope without building format-specific result graphs. Decoding a TOON document with the selected strict decoder SHALL yield the same JSON value as decoding JSON output for the same operation. Machine modes SHALL emit exactly one UTF-8 document to stdout with no ANSI, prompt, status, progress, or external log text; diagnostics SHALL go to stderr. Renderer selection SHALL NOT change operation execution, exception mapping, or exit code. Native Click parse failures that occur before output-mode resolution SHALL retain Click's stderr usage output and exit code `2`.

For `env remove`, JSON and TOON document modes (including the `--json` alias) SHALL never call `click.confirm`. Without `--yes`, they SHALL NOT execute removal and SHALL emit exactly one sanitized failure envelope with `error.code="confirmation_required"` and exit code `1`. With `--yes`, JSON and TOON SHALL execute the same removal operation and normal success/failure mapping. Interactive Rich mode SHALL retain its existing confirmation behavior.

Rich renderers SHALL remain adjacent to the concrete commands whose typed results they render. They MAY use `Table`, `Status`, `Progress`, and `Live` only when appropriate to the operation; they SHALL NOT introduce a generic renderer interface, registry, or DSL.

#### Scenario: JSON envelope

- **WHEN** `odcli env list --json` executes
- **THEN** stdout contains exactly one versioned envelope and no progress or log text

#### Scenario: JSON alias preserves envelope v1

- **WHEN** `odcli env list --json` and `odcli env list --format json` run against the same frozen result
- **THEN** each stdout document decodes to the same envelope v1 and contains no ANSI or diagnostic text

#### Scenario: TOON is semantically equal to JSON

- **WHEN** a bounded command succeeds or fails once and its envelope is emitted as JSON and TOON
- **THEN** strict TOON decoding and JSON decoding produce equal Python builtins including `result`/`data`, context, provenance, warnings, and error fields

#### Scenario: Conflicting alias is usage error

- **WHEN** a caller supplies `--json --format toon`
- **THEN** Click exits `2`, does not execute the operation, and does not emit a partial machine document

#### Scenario: Machine diagnostics stay on stderr

- **WHEN** a bounded machine-mode operation reports a sanitized diagnostic in addition to its result
- **THEN** stdout contains exactly one JSON or TOON envelope and the diagnostic is written only to stderr

#### Scenario: Machine remove requires explicit confirmation

- **WHEN** `odcli env remove ENV --format json`, `--format toon`, or `--json` is invoked without `--yes`
- **THEN** no prompt is rendered, removal is not called, stdout contains one failure envelope with `error.code="confirmation_required"`, and the command exits `1`

#### Scenario: Explicit machine remove executes

- **WHEN** `odcli env remove ENV --yes --format json`, `--format toon`, or `--json` is invoked
- **THEN** the same removal operation runs once and its result is emitted as one document under the normal renderer-independent exit mapping

#### Scenario: Secrets redacted

- **WHEN** error occurs during checkout
- **THEN** every machine or Rich error message redacts passwords, config body, and environment variables before emission

### Requirement: `odcli env list`

```bash
odcli env list [--format rich|json|toon]
odcli env list --all [--format rich|json|toon]
odcli env list --all-projects [--format rich|json|toon]
odcli env list --watch [--interval SECONDS]
```

The command SHALL invoke `EnvironmentMonitor.snapshot()` exactly once per one-shot rendering and once per live refresh, receiving one complete typed inventory. It SHALL NOT instantiate `OdooClient` to read backups/environments, query `BackupCatalog`, run Git/Docker/filesystem reconciliation, probe ports, regroup catalog rows into an alternative model, or perform any other collection after the snapshot returns. The renderer MAY group and sort the returned typed objects for presentation.

Rich output SHALL group by project: one project header and cluster summary followed by a Rich `Table` containing the environment rows for that project. It SHALL preserve the information represented by these columns: `NAME`, `BRANCH`, `STATE`, `RUNTIME`, `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, `GIT_AHEAD`, `GIT_DIFF`, `SIZE`, `DB_MODE`, `DATABASE`, `PORT`, and `ARTIFACTS`; responsive Rich layout MAY combine labels visually but SHALL NOT omit values. Rich color/ANSI SHALL be enabled only when supported by the output terminal.

`--all-projects` and project-context behavior SHALL remain unchanged. `--all` SHALL request `include_removed=True` only for Rich output so the existing observable contract remains: Rich includes removed rows, while JSON/TOON wrap the default non-removed `Snapshot`. JSON and TOON SHALL use `command="env.list"` and the same monitor snapshot contract as `GET /api/v1/snapshot`; TOON differs only in serialization.

Cluster, runtime/PID/resources, Git activity, storage, port observation, and artifact/backup availability SHALL all come from the returned snapshot. A project containing only removed rows MAY appear only when `include_removed=True`; its `environment_count` SHALL count the rows included in that result.

#### Scenario: Grouped by project with cluster header

- **WHEN** `env list` runs with two projects
- **THEN** output has two `Project <name>` headers each followed by a `PostgreSQL ...` cluster summary line, then that project's environment rows

#### Scenario: JSON parity with monitor snapshot

- **WHEN** `odcli env list --json --all-projects` runs
- **THEN** `result`/`data` payload uses the same `projects[].cluster` and `environments[].runtime` contract as `EnvironmentMonitor.snapshot()` and `GET /api/v1/snapshot`

#### Scenario: --all human includes removed, JSON does not

- **WHEN** `odcli env list --all` prints human table and `odcli env list --json --all` emits JSON
- **THEN** human table includes `STATE=removed` rows; JSON `result.environments` contains only non-removed snapshot rows

#### Scenario: Grouped Rich table uses one result

- **WHEN** Rich `env list` runs with two projects
- **THEN** output has two project sections and all displayed metrics/reconciliation fields originate from one `EnvironmentMonitor.snapshot()` result

#### Scenario: JSON and TOON parity with monitor snapshot

- **WHEN** `odcli env list --format json --all-projects` and `--format toon --all-projects` render the same sample
- **THEN** decoded `result`/`data` equal the JSON-safe `EnvironmentMonitor.snapshot()` object including cluster, runtime, observation, and artifacts

#### Scenario: --all compatibility

- **WHEN** `odcli env list --all` renders Rich and `odcli env list --all --format json` or `--format toon` renders a machine document
- **THEN** Rich includes `lifecycle_state="removed"` rows while both machine documents contain only non-removed rows

#### Scenario: CLI does not recollect inventory

- **WHEN** the `env list` command and renderers are exercised with a supplied typed snapshot
- **THEN** no CLI code opens the catalog, lists backups/environments, calls Git or Docker, probes a port, or performs filesystem reconciliation
