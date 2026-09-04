## ADDED Requirements

### Requirement: Project-only monitoring plans

Monitor planning SHALL include initialized projects from canonical project registration even when they have no environment catalogue rows. For each live project-owned runtime it SHALL validate stale-process identity and collect the same PID, worker PID, process count, CPU, RAM, readiness, URL, database, and applicable PostgreSQL cluster metrics used for environment-owned runtimes. Project filtering and deterministic ordering SHALL include both ownership kinds without creating synthetic environments.

#### Scenario: Initialized project without environments is visible
- **WHEN** the catalogue contains an initialized project and no environments
- **THEN** the snapshot includes the project and an empty environment list rather than returning an empty project list

#### Scenario: Live project runtime has metrics
- **WHEN** that project has a valid live runtime identity
- **THEN** its runtime/process/readiness/database fields and applicable cluster metrics appear in the typed snapshot

#### Scenario: Stale project PID is not reused
- **WHEN** the stored project PID exists but its create time does not match
- **THEN** it is reported as stale/stopped under the existing identity rules and unrelated process metrics are not exposed

### Requirement: Snapshot preserves environment compatibility

The snapshot SHALL represent project-owned runtimes additively while preserving existing environment arrays, environment runtime states, filtering, redaction, JSON serialization, and cache boundaries. Project runtime collection SHALL reuse the existing typed collector and process provider rather than adding a second monitor implementation.

#### Scenario: Mixed ownership snapshot
- **WHEN** one project-owned runtime and existing environment-owned runtimes are live
- **THEN** all are represented deterministically and current environment consumers retain their existing fields
