## MODIFIED Requirements

### Requirement: PostgreSQL backend attribution

Backend attribution SHALL be bounded to the existing safe PostgreSQL boundary and `pg_stat_activity`. One Odoo instance holding multiple connections SHALL be modelled as a group of backend processes, not a single PostgreSQL PID. A backend group SHALL be attached to the main checkout or an environment only when database ownership is unambiguous in the current snapshot. When one database is used by multiple checkouts, the group SHALL appear once under shared resources with reason `shared_database`.

For native Linux or external PostgreSQL, per-backend CPU/RSS SHALL be reported on host-visible PIDs only after PID plus create-time verification and privilege availability. For PostgreSQL inside Docker Desktop or Colima on macOS, backend PIDs SHALL be marked VM-scoped: PID and count SHALL be shown when available, but per-backend CPU/RSS SHALL be unavailable, with the container's Docker stats as the authoritative shared metric. A `pg_stat_activity` error, timeout, privilege failure, invalid response, bounded output-limit failure, or unavailable transport SHALL degrade only the affected PostgreSQL backend group with a typed unavailability reason. Arbitrary system postgres processes SHALL NOT be scanned and the Odoo client PID SHALL NOT be guessed.

Every `pg_stat_activity` process launch that `EnvironmentMonitor.processes_command()` can consume SHALL be captured before execution as an exact private `PreparedStep` in that command's immutable plan. The captured step SHALL preserve argv boundaries, sanitized environment, timeout, finite per-stream and combined output limits, stdin, private credential inputs, and the existing redacted public projection. When `psql` cannot be resolved at construction time, command construction SHALL still succeed by using the canonical PostgreSQL builder's deferred-capability mode and SHALL expose literal `psql` as the exact executable in the inspectable captured step. Execution SHALL consume each planned attribution step through the shared process boundary at most once; a spawn failure of that captured executable SHALL produce typed `psql_missing`, a process timeout SHALL produce typed `timeout`, and a concrete `ProcessOutputLimitError` SHALL produce typed `query_failed` after child termination. It SHALL NOT construct a replacement step inside an active `RunContext`, bypass ledger validation, add a second process runner, or translate `UnplannedStepError` or `DuplicateStepError` into availability degradation.

#### Scenario: Unique database attribution

- **WHEN** one environment owns a database unambiguously
- **THEN** that environment's block shows the backend group with PID, count, state, and connection identity

#### Scenario: Shared database shown once

- **WHEN** two environments use the same database
- **THEN** the backend group appears once under shared resources with reason `shared_database`

#### Scenario: macOS Docker backend PID is VM-scoped

- **WHEN** PostgreSQL runs inside Docker Desktop on macOS
- **THEN** backend PID and count are shown but per-backend CPU/RSS are unavailable with an explicit reason

#### Scenario: Healthy attribution consumes one preplanned step

- **WHEN** `processes_command()` captures a selected project with one healthy owned cluster, one database, and usable credentials and then executes with a recording process executor
- **THEN** its immutable plan already contains the exact `process_inventory.PROJECT_ID.DATABASE_NAME.pg_stat_activity` process step and execution consumes that step exactly once without `UnplannedStepError` or `DuplicateStepError`

#### Scenario: Failed attribution degrades only its backend group

- **WHEN** a captured PostgreSQL attribution step times out, returns a non-zero result, returns a malformed successful response, exceeds its bounded output limit, or cannot use the captured transport
- **THEN** the command still returns the remaining `ProcessInventory`, preserves unrelated backend groups and Odoo facts, and marks only the affected backend group with `timeout`, the classified non-zero reason, `invalid_response`, `query_failed`, or the matching transport reason

#### Scenario: Missing psql remains inspectable and degrades at execution

- **WHEN** usable database credentials exist but `psql` cannot be resolved while `processes_command()` is constructed, and execution cannot spawn the captured literal executable
- **THEN** construction succeeds with an exact inspectable `pg_stat_activity` step whose executable is `psql`, execution returns the remaining `ProcessInventory`, and only the affected backend group reports `psql_missing`

#### Scenario: Ledger violations are not availability failures

- **WHEN** execution requests an attribution step that differs from the captured step or consumes the same step twice
- **THEN** `UnplannedStepError` or `DuplicateStepError` propagates and is not converted to `psql_missing`, `timeout`, or another backend unavailability reason

#### Scenario: One captured inventory projects to every format

- **WHEN** the successful `ps` result is rendered as Rich, JSON, or TOON
- **THEN** all formats project the same single captured inventory without an extra monitor snapshot or backend sample
