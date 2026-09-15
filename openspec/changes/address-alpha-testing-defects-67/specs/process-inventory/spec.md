## Purpose

One read-only process and resource inventory that shows CPU, memory, process counts, and storage ownership for shared project resources, the main checkout, and every environment worktree, projected from a single canonical `EnvironmentMonitor` snapshot per sample.

## ADDED Requirements

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