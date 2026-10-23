## ADDED Requirements

### Requirement: Bounded safe catalogue read projections

`BackupCatalog` SHALL provide typed read-only page queries for backup summaries/events, recorded incomplete operations/errors, and environment history. Page sizes SHALL have a finite maximum; ordering SHALL be stable by timestamp plus sequence or identity; filters SHALL be typed exact values or date ranges and SHALL never be interpolated SQL. These queries SHALL reuse the authoritative catalogue connection/migrations and SHALL NOT create a reporting catalogue or mutate lifecycle rows.

Backup summaries SHALL include safe project identity/display, source label, database, format, size, lifecycle status, and `downloaded_at`. Incomplete-operation rows SHALL include safe object identity/type, recorded state, timestamp, and sanitized error. Environment-history rows SHALL include safe project/environment identity, timestamp, operation, and result/state.

#### Scenario: Equal timestamps paginate deterministically

- **WHEN** more than one matching row has the same displayed timestamp
- **THEN** the stable secondary key SHALL produce deterministic bounded pages without arbitrary ordering

#### Scenario: Filters are parameterized

- **WHEN** a filter contains control characters, quotes, wildcard symbols, or an unknown enum value
- **THEN** the query SHALL validate or bind it as data and SHALL NOT change SQL structure or expose raw database errors

#### Scenario: Read projection selects no unsafe column

- **WHEN** the authoritative row also contains paths, JSON payloads, raw messages, or command output
- **THEN** those fields SHALL not be selected into the public read model

### Requirement: Current database backup provenance is queryable

The catalogue SHALL resolve backup freshness only from the latest completed current provenance for the exact cluster identity, host, port, and database. A qualifying provenance row SHALL be a catalogue-source restore linked to a surviving backup with nonnull `downloaded_at`. Local archives, bootstraps, incomplete restores, dropped/replaced databases, cluster mismatches, missing backups, and null download times SHALL return an explicit unknown result.

#### Scenario: Completed catalogue restore resolves download time

- **WHEN** the current exact database identity has a latest completed catalogue restore linked to a backup with `downloaded_at`
- **THEN** the query SHALL return that backup identity and download timestamp

#### Scenario: Later replacement invalidates older provenance

- **WHEN** a later drop, bootstrap, local-archive restore, incomplete replacement, or different-cluster identity supersedes an older catalogue restore
- **THEN** the older backup download timestamp SHALL NOT be reported as current

#### Scenario: Unknown provenance remains explicit

- **WHEN** no qualifying exact completed provenance exists
- **THEN** the query SHALL return unknown without inferring from database names, environment backup IDs, or file timestamps
