## ADDED Requirements

### Requirement: ProcessInventory projection from one snapshot

`EnvironmentMonitor` SHALL expose `processes_command(project_id: str | None = None) -> Command[ProcessInventory]` and a convenience `processes(project_id: str | None = None) -> ProcessInventory`. Both SHALL use one captured canonical snapshot; the convenience method SHALL NOT perform a second sample. `ProcessInventory` SHALL be a frozen `msgspec.Struct(frozen=True, forbid_unknown_fields=True, kw_only=True)` and SHALL be the single projection for Rich, JSON, and TOON.

`ProcessInventory` SHALL represent three ownership kinds in this order: shared project resources, the main checkout, and each non-removed environment. The main checkout's Odoo process group SHALL be surfaced from `ProjectSummary.runtime` and SHALL NOT be collected silently. Stopped checkouts and environments SHALL remain visible with runtime metrics marked unavailable and storage still reported. Shared Git objects and the PostgreSQL volume SHALL appear exactly once and SHALL NOT be added to each environment. Root Odoo and recursive workers SHALL be aggregated exactly once using the existing recursive process tree.

#### Scenario: One snapshot per process inventory sample

- **WHEN** `monitor.processes()` is called
- **THEN** it captures one canonical snapshot and projects it into `ProcessInventory` without a second sample

#### Scenario: Main checkout runtime surfaced

- **WHEN** the main checkout has a running Odoo runtime
- **THEN** `ProcessInventory` shows that runtime under the main checkout block

### Requirement: PostgreSQL backend attribution bounded to pg_stat_activity

Backend attribution SHALL be bounded to the existing safe PostgreSQL boundary and `pg_stat_activity`. One Odoo instance holding multiple connections SHALL be modelled as a group of backend processes. A backend group SHALL be attached to the main checkout or an environment only when database ownership is unambiguous in the current snapshot. When one database is used by multiple checkouts, the group SHALL appear once under shared resources with reason `shared_database`. Backend RSS SHALL NOT be added to the container's total memory.

For native Linux or external PostgreSQL, per-backend CPU/RSS SHALL be reported on host-visible PIDs only after PID plus create-time verification and privilege availability. For PostgreSQL inside Docker Desktop or Colima on macOS, backend PIDs SHALL be marked VM-scoped: PID and count MAY be shown, but per-backend CPU/RSS SHALL be unavailable. A `pg_stat_activity` error or privilege failure SHALL degrade only the PostgreSQL portion of the row. Arbitrary system postgres processes SHALL NOT be scanned and the Odoo client PID SHALL NOT be guessed.

#### Scenario: Unique database attribution

- **WHEN** one environment owns a database unambiguously
- **THEN** that environment's block shows the backend group with PID, count, state, and connection identity

#### Scenario: Shared database shown once

- **WHEN** two environments use the same database
- **THEN** the backend group appears once under shared resources with reason `shared_database`

#### Scenario: macOS Docker backend PID is VM-scoped

- **WHEN** PostgreSQL runs inside Docker Desktop on macOS
- **THEN** backend PID and count are shown but per-backend CPU/RSS are unavailable with an explicit reason

### Requirement: Bounded external process contribution

One bounded frozen process-contribution contract SHALL allow external sources to add typed process groups to `ProcessInventory`. Each contribution SHALL contain `source`, stable local identity, lifecycle state, owner kind `shared | project | environment`, canonical owner ID, confirmed root/child PIDs and PID scope when observable, CPU/RSS, sample time, and explicit availability/completeness. Contributions SHALL NOT carry prompts, transcripts, secrets, raw command lines, or environment values. Both sources SHALL reuse core PID identity/metrics collection and deduplication: one PID has one owner and a shared process is shown once. A provider SHALL NOT create its own sampler, live loop, serializer, or process hierarchy. This contract SHALL NOT be generalized into a plugin or lifecycle framework.

#### Scenario: External process group attributed once

- **WHEN** an external process contribution is available
- **THEN** `ProcessInventory` contains one typed process group with a single owner and no duplicate PID

#### Scenario: No secrets in contribution

- **WHEN** a process contribution is inspected
- **THEN** it contains no prompt, transcript, secret, raw command line, or environment value