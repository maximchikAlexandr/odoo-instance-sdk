## ADDED Requirements

### Requirement: Backup reads remain observational

Backup `list()` and `latest()` SHALL inspect catalogue eligibility and filesystem availability without changing backup state, appending events, backfilling ownership or creating missing markers. Repeated polling SHALL be idempotent with respect to catalogue bytes.

#### Scenario: Missing backup file is only observed

- **WHEN** an available catalogue row points to an absent or unreadable file
- **THEN** list/latest exclude it according to existing eligibility rules and perform no catalogue write

#### Scenario: Repeated latest is inert

- **WHEN** automation calls `latest()` repeatedly with identical catalogue/filesystem state
- **THEN** it receives the same result and catalogue events/rows remain unchanged

### Requirement: Structured durable replacement recovery

The environment catalogue SHALL provide nullable `recovery_json` encoded from one versioned frozen `CopyReplacementRecovery` DTO. It SHALL contain only bounded secret-free identities and observed recovery state needed by existing DB replacement compensation. `last_error` SHALL remain a sanitized human diagnostic and SHALL NOT be parsed as executable recovery input for newly written failures.

#### Scenario: Failed compensation persists structure atomically

- **WHEN** DB replacement cannot fully compensate
- **THEN** environment state, bounded `last_error`, structured `recovery_json` and the failure event are committed atomically

#### Scenario: Retry consumes only validated recovery

- **WHEN** replacement retry or repair begins with structured recovery
- **THEN** it validates exact environment, backup, cluster, database and filestore identities and live state before using existing compensation steps

#### Scenario: Successful recovery clears evidence

- **WHEN** compensation or replacement publication reaches all required postconditions
- **THEN** `recovery_json` is cleared atomically with the ready state while journals and prior events remain preserved

### Requirement: Legacy replacement evidence migrates only at explicit repair

An existing `cleanup_failed` row lacking `recovery_json` MAY be converted only by the explicit replacement repair operation from the known bounded legacy `retained=...` form. The operation SHALL validate the parsed version and every retained identity before persisting structured recovery. Unknown, malformed, secret-bearing or contradictory text SHALL fail closed without mutation.

#### Scenario: Known legacy evidence is adopted

- **WHEN** explicit repair selects a known legacy row whose retained identities match current catalogue and live state
- **THEN** it persists the equivalent versioned recovery DTO and continues through the existing guarded compensation path

#### Scenario: Unknown legacy text is rejected

- **WHEN** `last_error` does not match the exact known legacy form or its identities do not match
- **THEN** no recovery state is persisted, no database/filestore mutation occurs and a sanitized actionable error is returned

### Requirement: Catalogue migration preserves compatibility

The single Alembic lineage SHALL add nullable recovery storage without rewriting unrelated environment rows, changing existing event history or introducing a second catalogue. Opening an older supported catalogue SHALL migrate idempotently; reopening current head SHALL make no schema/data change.

#### Scenario: Existing catalogue migrates

- **WHEN** a supported pre-change catalogue is opened
- **THEN** the nullable recovery field is added, all prior rows/events remain intact and Alembic reaches one head

#### Scenario: Migration retry is idempotent

- **WHEN** migration is retried or a current catalogue is reopened
- **THEN** there is one recovery field, one Alembic head and no duplicated or altered recovery/event data
