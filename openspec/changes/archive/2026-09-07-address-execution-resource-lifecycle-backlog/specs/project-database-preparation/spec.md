## ADDED Requirements

### Requirement: One preparation pipeline accepts remote or catalogue source

Project database preparation SHALL select exactly one internal source variant: the existing remote backup source or an exact registered catalogue backup UUID. Both variants SHALL support an external PostgreSQL binding and a legacy Compose target without an authoritative ownership claim, and SHALL converge on the same target reservation, validation, restore, neutralization, optional administrator reset, postcondition, nullable identity-aware audit, failure-retention, and atomic default-switch stages. For a currently inspected target matching an authoritative `active` managed Compose claim, the common audit transaction SHALL record its exact non-null `cluster_id` on the completed restore and restored-event rows. For an external or no-claim legacy Compose target, that transaction SHALL record `cluster_id=null`/unknown; this SHALL remain valid restore history but SHALL NOT prove ownership. An existing `pending` claim SHALL refuse either restore source before database mutation or completed audit, and malformed or mismatched evidence for an existing claim SHALL fail closed rather than be treated as legacy. The catalogue variant SHALL skip every remote-download stage rather than implement a second restore pipeline.

#### Scenario: Remote refresh supports an external target

- **WHEN** existing `db refresh --restore` selects the remote source for an external PostgreSQL binding with no ownership claim
- **THEN** it performs the established download and shared downstream stages, and records completed provenance with `cluster_id=null`/unknown

#### Scenario: Catalogue restore supports a legacy Compose target

- **WHEN** preparation selects an available catalogue UUID for a legacy Compose target with no authoritative claim
- **THEN** no remote Odoo backup endpoint is called, the shared downstream stages consume the existing file, and completed provenance records `cluster_id=null`/unknown

#### Scenario: Active managed target records exact identity

- **WHEN** either restore source targets a currently inspected Compose cluster matching an `active` claim
- **THEN** completed provenance records that same non-null `cluster_id` on the exact restore and restored-event rows

#### Scenario: Pending claim blocks both restore sources

- **WHEN** the current cluster claim is `pending` for either remote refresh restore or catalogue restore
- **THEN** preparation refuses before database mutation or completed restore audit

#### Scenario: Nullable provenance does not grant destructive authority

- **WHEN** a completed external or legacy restore has `cluster_id=null`/unknown
- **THEN** it remains readable restore history but cannot satisfy any later `db drop` ownership gate

### Requirement: Local restore preflight is mutation-free

Before the first local-restore mutation, preparation SHALL validate exact UUID and available state, stable file identity and checksum/format, local project PostgreSQL binding, safe target syntax, target absence, and restoration capability while holding the project preparation and backup lifecycle locks. Any failure SHALL create no database and change no project configuration.

#### Scenario: Preflight checksum differs

- **WHEN** the registered file content no longer matches its catalogue checksum
- **THEN** preparation fails before invoking restore and retains the prior project default

#### Scenario: Default target generation

- **WHEN** no target is supplied
- **THEN** preparation selects and reserves a safe collision-free new database name without deleting an older database

### Requirement: Preparation exposes truthful long-step progress

Remote wait/download, validation, restore, neutralization, administrator reset, postcondition, and default switch SHALL expose their applicable planned step lifecycle through the shared observer. Completion of each logical action SHALL be emitted only after that action's effect and postcondition.

#### Scenario: Restore postcondition fails

- **WHEN** the restore request returns but the target database cannot be confirmed
- **THEN** the restore step is reported failed rather than completed and default switching does not start

#### Scenario: Dry-run preparation

- **WHEN** either source variant is previewed
- **THEN** the immutable plan identifies the selected source and target but no runtime progress or effect occurs
