# process-inventory Specification

## Purpose
TBD - created by archiving change address-alpha-testing-defects-67. Update Purpose after archive.
## Requirements
### Requirement: Single process inventory leaf

The CLI SHALL provide exactly one read-only leaf `odcli ps [--all-projects] [--watch] [--interval SECONDS] [--format rich|json|toon]` that projects process and resource usage. A separate `odcli top` command SHALL NOT exist. The command SHALL NOT start, stop, kill, or register any process. `--watch` SHALL be a live/htop-like mode of the same command and SHALL reuse the existing Rich live loop pattern. `--watch` SHALL be valid only for `rich` mode when stdout is an interactive TTY and SHALL reject machine modes and non-interactive stdout with the same exit codes as `env list --watch`. `--interval` SHALL default to `2.0` and SHALL reject values below `0.1` as a Click usage error with exit code `2`.

The command SHALL accept `--all-projects` to scope across all registered projects and `--fields` following the existing typed field projection contract from issue #62. A second selector contract SHALL NOT be introduced.

#### Scenario: One canonical snapshot per sample

- **WHEN** `odcli ps` renders one sample
- **THEN** it calls `EnvironmentMonitor.snapshot_command()` exactly once and projects that single result into Rich, JSON, or TOON

#### Scenario: Watch reuses the live loop

- **WHEN** `odcli ps --watch --interval 2` runs in an interactive terminal
- **THEN** one Rich live view refreshes from successive canonical snapshots with the same filters and no second collector

#### Scenario: Watch rejects machine output

- **WHEN** `odcli ps --watch --format json` is invoked
- **THEN** Click exits `2` without starting a live loop or emitting a partial machine document

### Requirement: Frozen ProcessInventory typed model

The SDK SHALL expose one frozen `msgspec.Struct(frozen=True, forbid_unknown_fields=True, kw_only=True)` `ProcessInventory` model that is the single projection for Rich, JSON, and TOON. It SHALL carry stable project, environment, and owner IDs, PID scope, sample time, completeness flags, and explicit unavailable reasons. Rich, JSON, and TOON SHALL project the same model; three different field sets SHALL NOT exist.

The model SHALL represent three ownership kinds in this order: shared project resources, the main checkout, and each non-removed environment. Stopped checkouts and environments SHALL remain visible with runtime metrics marked unavailable and storage still reported. Shared Git objects and the PostgreSQL volume SHALL appear exactly once and SHALL NOT be added to each environment.

#### Scenario: Stopped owner stays visible

- **WHEN** `odcli ps` runs and the main checkout has no running Odoo process
- **THEN** the main checkout row appears with runtime metrics marked unavailable and its storage footprint still reported

#### Scenario: Shared volume is not double-counted

- **WHEN** two environments share the same PostgreSQL volume
- **THEN** the volume appears once under shared resources and is not added to either environment's storage

### Requirement: Odoo process aggregation

Root Odoo and recursive worker processes SHALL be aggregated exactly once in both single-process and multiprocess modes using the existing recursive process tree. CPU and RSS of all related workers SHALL be summed once. A PID SHALL have exactly one owner. The main checkout's Odoo process group SHALL be surfaced from `ProjectSummary.runtime` and SHALL NOT be collected silently.

#### Scenario: Multiprocess workers summed once

- **WHEN** an Odoo instance runs with multiple workers
- **THEN** the process group reports one aggregated CPU and one aggregated RSS covering all workers

#### Scenario: Main checkout runtime is visible

- **WHEN** `odcli ps` runs and the main checkout has a running Odoo runtime
- **THEN** that runtime appears in the main checkout block rather than being hidden

### Requirement: PostgreSQL container stays project-shared

The managed PostgreSQL container's CPU, RAM, and volume SHALL remain project-shared and SHALL be shown exactly once under shared resources. Backend RSS SHALL NOT be added to the container's total memory. The container's volume SHALL NOT be added to any environment's storage.

#### Scenario: Container metrics are shared

- **WHEN** `odcli ps` runs against a project with a managed PostgreSQL container
- **THEN** the container's CPU, RAM, and volume appear once under shared resources and are not repeated per environment

### Requirement: PostgreSQL backend attribution

Backend attribution SHALL be bounded to the existing safe PostgreSQL boundary and `pg_stat_activity`. One Odoo instance holding multiple connections SHALL be modelled as a group of backend processes, not a single PostgreSQL PID. A backend group SHALL be attached to the main checkout or an environment only when database ownership is unambiguous in the current snapshot. When one database is used by multiple checkouts, the group SHALL appear once under shared resources with reason `shared_database`.

For native Linux or external PostgreSQL, per-backend CPU/RSS SHALL be reported on host-visible PIDs only after PID plus create-time verification and privilege availability. For PostgreSQL inside Docker Desktop or Colima on macOS, backend PIDs SHALL be marked VM-scoped: PID and count MAY be shown, but per-backend CPU/RSS SHALL be unavailable, with the container's Docker stats as the authoritative shared metric. A `pg_stat_activity` error or privilege failure SHALL degrade only the PostgreSQL portion of the row. Arbitrary system postgres processes SHALL NOT be scanned and the Odoo client PID SHALL NOT be guessed.

#### Scenario: Unique database attribution

- **WHEN** one environment owns a database unambiguously
- **THEN** that environment's block shows the backend group with PID, count, state, and connection identity

#### Scenario: Shared database shown once

- **WHEN** two environments use the same database
- **THEN** the backend group appears once under shared resources with reason `shared_database`

#### Scenario: macOS Docker backend PID is VM-scoped

- **WHEN** PostgreSQL runs inside Docker Desktop on macOS
- **THEN** backend PID and count are shown but per-backend CPU/RSS are unavailable with an explicit reason

### Requirement: Bounded process contribution contract

One bounded frozen process-contribution contract SHALL allow two known external sources (`odcli-codex` from #68 and the Multica daemon from #70) to add typed process groups to `ProcessInventory`. Each contribution SHALL contain `source`, a stable local identity, lifecycle state, owner kind `shared | project | environment`, canonical owner ID, confirmed root/child PIDs and PID scope when observable, CPU/RSS, sample time, and explicit availability/completeness information. Contributions SHALL NOT carry prompts, transcripts, secrets, raw command lines, or environment values.

Both sources SHALL reuse the core PID identity/metrics collection and deduplication: one PID has one owner and a shared process is shown once. A provider SHALL NOT create its own sampler, live loop, serializer, or process hierarchy. This contract SHALL NOT be generalized into a plugin or lifecycle framework; it exists only to add bounded process groups to `ProcessInventory`.

#### Scenario: External process group is attributed

- **WHEN** an `odcli-codex` process is running and its contribution is available
- **THEN** `ProcessInventory` contains one typed process group owned by the matching environment without core depending on the Codex SDK

#### Scenario: No transcript or secrets in contribution

- **WHEN** a process contribution is inspected
- **THEN** it contains no prompt, transcript, secret, raw command line, or environment value

### Requirement: Python SDK process primitives

`EnvironmentMonitor` SHALL expose `processes_command(project_id: str | None = None) -> Command[ProcessInventory]` and a convenience `processes(project_id: str | None = None) -> ProcessInventory`. Both SHALL use one captured canonical snapshot; the convenience method SHALL NOT perform a second sample.

#### Scenario: SDK processes command is captured

- **WHEN** `monitor.processes_command()` is called
- **THEN** it returns one immutable command that captures the snapshot inputs without sampling

#### Scenario: SDK convenience does not re-sample

- **WHEN** `monitor.processes()` is called
- **THEN** it delegates to `processes_command()` and does not perform a second snapshot

### Requirement: One common process table per inventory section

The Rich projection of `ProcessInventory` SHALL preserve the existing shared-resource, main-checkout, and environment sections and their deterministic order. Within each section it SHALL render exactly one common bordered process table with primary columns `Type`, `State`, `PID / scope`, `Processes`, `CPU`, `Memory`, and `Details`. Odoo groups, the proven-owned PostgreSQL cluster/container, attributable PostgreSQL backend groups, and bounded external process contributions SHALL use these same columns instead of product-specific table shapes or prose.

All process tables rendered together in one Rich frame SHALL use the same ordered width allocation for those seven named columns. Each column's unconstrained width SHALL account for the widest header or sanitized cell line in every process section in that frame. When the unconstrained grid exceeds the available terminal width, the projection SHALL apply one deterministic constrained allocation to the whole frame and SHALL wrap content without dropping a section, row, column, or typed fact. The resulting outer table widths and corresponding vertical column boundaries SHALL match across every process table in the frame.

One-shot `odcli ps` SHALL derive the allocation from its single rendered inventory. Every successful `odcli ps --watch` refresh SHALL derive a fresh allocation from that refresh's inventory and current console width before replacing the live frame; it SHALL NOT reuse widths from a prior sample when content or terminal width changes.

Storage footprint MAY remain a separate section fact and SHALL NOT be represented as a process row. A section with no proven process entry SHALL still render an explicit empty/unavailable row in its common table rather than omitting the table or inventing a process.

#### Scenario: Mixed checkout uses one table

- **WHEN** one checkout has an Odoo process group, an attributable PostgreSQL backend group, and an external contribution
- **THEN** its section contains one bordered table with three rows under the same seven primary columns

#### Scenario: Owned cluster uses the common shape

- **WHEN** a shared-resource section contains a proven-owned PostgreSQL cluster/container
- **THEN** that cluster is one row in the section's common process table and is not emitted as a separate prose line or incompatible table

#### Scenario: Empty stopped checkout stays visible

- **WHEN** a stopped checkout has no running process and its typed inventory keeps the owner visible
- **THEN** its section contains one explicit stopped/unavailable row in the common table

#### Scenario: One-shot frame aligns unequal sections

- **WHEN** `odcli ps` renders two or more process sections whose corresponding cells require different natural widths at a fixed terminal width
- **THEN** every same-named column has identical left and right boundary positions across all process tables and all rows and facts remain present, wrapping where required

#### Scenario: Single section retains the common projection

- **WHEN** one Rich frame contains exactly one process section
- **THEN** that section renders one bounded table with the same seven columns and no placeholder or second table is introduced for layout coordination

#### Scenario: Watch recalculates one grid per refresh

- **WHEN** `odcli ps --watch` receives a later successful sample with wider cell content or a changed console width
- **THEN** the replacement frame uses one newly calculated width allocation across all of its process tables and does not mix boundaries from the preceding frame

### Requirement: Product-specific process facts belong in Details

The common table SHALL map type-specific facts into `Details` without creating additional primary columns. An Odoo row SHALL retain database, endpoint, branch, and commit when available. An owned PostgreSQL cluster row SHALL retain endpoint, server version, active/total connections, container identity, and typed availability reasons when available. A PostgreSQL backend-group row SHALL retain database, connection count, attribution reason, and connection identity when available. An external-contribution row SHALL retain source, stable local identity, and availability/completeness reason.

The `PID / scope` cell SHALL distinguish host-visible, Docker-VM, and unavailable scopes. `Processes`, CPU, and memory SHALL use only values present in the typed inventory; unavailable values SHALL remain explicit and SHALL NOT be derived from unrelated container or Odoo metrics.

#### Scenario: Odoo details preserve identity

- **WHEN** an Odoo group has database, endpoint, branch, and commit facts
- **THEN** the Odoo row places those facts in `Details` and keeps the shared primary columns unchanged

#### Scenario: Backend group explains attribution

- **WHEN** a backend group is attributed by `unique_database`
- **THEN** its row shows the database, connection count, and attribution reason in `Details` and uses its typed PID scope and metrics

#### Scenario: Docker VM metrics remain unavailable

- **WHEN** a PostgreSQL backend group has Docker-VM PID scope and no host-visible CPU or memory
- **THEN** the row shows Docker-VM scope, explicit unavailable CPU/memory, and does not copy container aggregate metrics into the backend row

#### Scenario: External contribution explains availability

- **WHEN** a bounded external contribution is unavailable or partial
- **THEN** its row retains source and stable identity when present and displays the typed availability/completeness reason in `Details`

### Requirement: Process table projection does not change inventory semantics

The unified human table SHALL be a pure projection of the existing frozen `ProcessInventory`. It SHALL NOT add a collector, re-sample a snapshot, scan arbitrary system processes, guess Odoo client PIDs, infer ownership, or change JSON/TOON field sets. One PID SHALL retain the single owner assigned by the inventory, and shared resources SHALL remain shown exactly once.

#### Scenario: Rich projection performs no collection

- **WHEN** a frozen `ProcessInventory` is rendered as Rich
- **THEN** no monitor, Docker, PostgreSQL, filesystem, Git, or process collector is invoked

#### Scenario: Shared backend is not duplicated

- **WHEN** a backend group is owned by shared resources because its database is used by multiple checkouts
- **THEN** it appears once in the shared section table and in no checkout table

#### Scenario: Machine projections remain identical

- **WHEN** the same frozen `ProcessInventory` is emitted as JSON or TOON after the Rich redesign
- **THEN** its decoded machine result is unchanged from the pre-redesign typed projection
