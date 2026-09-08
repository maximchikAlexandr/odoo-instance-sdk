## ADDED Requirements

### Requirement: Environment child foreign keys survive catalogue migration

The next applicable sequential catalogue migration SHALL ensure `environment_events.environment_id` and `environment_copy_journal.environment_id` reference the current `environments` table rather than a removed staging table such as `environments_v7`. It SHALL preserve all child and parent rows, indexes, constraints, and identifiers, advance `PRAGMA user_version` exactly once, remain retry-safe through the existing migration transaction/lock, and create fresh catalogues with the correct references directly. If the approved base already contains this migration, implementation SHALL retain it and add the missing behavioral regression coverage rather than create a duplicate version.

#### Scenario: V7 catalogue migrates with child rows intact

- **WHEN** a v7 catalogue containing environments, events, and copy-journal rows is opened at the current schema version
- **THEN** all rows remain, both foreign keys target `environments`, and `user_version` advances to the current sequential value

#### Scenario: New event proves repaired reference

- **WHEN** migration from v7 to current completes and a new event is inserted for an existing environment with foreign keys enabled
- **THEN** the insert succeeds and the new event is readable

#### Scenario: Fresh catalogue has current references

- **WHEN** a new catalogue is created
- **THEN** both environment child tables reference `environments` without a staging-table name
