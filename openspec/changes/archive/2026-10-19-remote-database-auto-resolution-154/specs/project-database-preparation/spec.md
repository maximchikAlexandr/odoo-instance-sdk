## ADDED Requirements

### Requirement: COPY refuses a named source without a database

COPY preparation SHALL require either an explicitly configured remote database or an explicit retained backup. If the selected named remote omits its database and no backup is supplied, the operation SHALL fail before download, restore, database creation, or default publication and SHALL identify `db refresh --remote` as the supported resolution path.

#### Scenario: Named COPY without database is rejected

- **WHEN** a caller selects a named remote with no database for COPY checkout
- **THEN** the command returns the documented refusal before any source request or local mutation

#### Scenario: Explicit backup remains supported

- **WHEN** a caller selects the same named remote without a database but supplies an explicit retained backup
- **THEN** COPY preparation may proceed through the existing backup validation and restore contract

### Requirement: Captured Git validation guards generated configuration publication

Before publishing a restored project default or generated configuration, preparation SHALL consume the exact read-only Git checks captured during planning. A tracked target, failed check, changed repository root, or changed captured argv SHALL fail closed before publication.

#### Scenario: Tracked generated configuration blocks publication

- **WHEN** the captured Git check reports that the generated configuration is tracked
- **THEN** restore fails before changing the default or publishing the configuration

#### Scenario: Captured repository drift blocks publication

- **WHEN** execution observes a changed repository root or a different captured Git command
- **THEN** restore refuses publication without exposing secrets
