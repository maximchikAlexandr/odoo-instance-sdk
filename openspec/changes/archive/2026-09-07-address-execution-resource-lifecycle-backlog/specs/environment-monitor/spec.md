## ADDED Requirements

### Requirement: Reusable local resource measurements

Local resource inventory SHALL reuse the existing monitor/storage-footprint probes and stable project/environment/cluster identities for worktrees, Python environments, logs, database logical size, and Compose volumes. The monitor snapshot/API SHALL retain its current redaction and path-free contract; the explicit local maintenance projection MAY add sanitized paths from the catalogue or project manifest without modifying monitor DTOs. Shared Python environments and volumes SHALL be de-duplicated by stable identity.

#### Scenario: Shared Python environment

- **WHEN** two environments reference the same external or shared Python environment
- **THEN** local resource totals count its measured bytes at most once and both relationships remain visible

#### Scenario: Monitor API remains path-free

- **WHEN** resource inventory adds a local path needed for diagnosis
- **THEN** the existing monitor snapshot and HTTP API still expose no absolute worktree, Python environment, repository, backup, log, or filestore path

#### Scenario: Measurement is incomplete

- **WHEN** a filesystem, PostgreSQL, or Docker probe is unavailable
- **THEN** the affected resource reports incomplete measurement and reason while unrelated resources remain present

### Requirement: Read-only observations do not reconcile lifecycle state

Monitor-derived resource inspection SHALL not translate a missing file, database, environment artifact, container, or volume into a deletion audit event. Lifecycle state changes SHALL occur only through their explicit mutating commands after verified postconditions.

#### Scenario: Missing backup observed

- **WHEN** resource inspection finds an available catalogue backup whose file is missing
- **THEN** it reports the mismatch without marking the backup deleted

#### Scenario: Database probe is partial

- **WHEN** PostgreSQL inventory returns an error or incomplete result
- **THEN** no database is marked dropped and resource completeness is false
