## MODIFIED Requirements

### Requirement: One preparation pipeline accepts remote or catalogue source

Project database preparation SHALL select exactly one typed source variant: the existing remote backup source, an exact registered catalogue backup UUID, or a public `LocalArchiveRestoreSource` containing a local path. All variants SHALL support an external PostgreSQL binding and a legacy Compose target without an authoritative ownership claim, and SHALL converge on the same target reservation, validation, restore, neutralization, optional administrator reset, postcondition, source-neutral identity-aware audit, failure-retention, progress, cleanup, and atomic default-switch stages. For a currently inspected target matching an authoritative `active` managed Compose claim, the common audit transaction SHALL record its exact non-null `cluster_id` on the completed restore and restored-event rows. For an external or no-claim legacy Compose target, that transaction SHALL record `cluster_id=null`/unknown; this SHALL remain valid restore history but SHALL NOT prove ownership. An existing `pending` claim SHALL refuse every source before database mutation or completed audit, and malformed or mismatched evidence for an existing claim SHALL fail closed rather than be treated as legacy. Catalogue and local-archive variants SHALL skip every remote-download stage rather than implement parallel restore pipelines. The local-archive variant SHALL create neither a backup catalogue row nor a retained copy.

#### Scenario: Remote refresh supports an external target

- **WHEN** existing `db refresh --restore` selects the remote source for an external PostgreSQL binding with no ownership claim
- **THEN** it performs the established download and shared downstream stages, and records completed provenance with `cluster_id=null`/unknown

#### Scenario: Catalogue restore supports a legacy Compose target

- **WHEN** preparation selects an available catalogue UUID for a legacy Compose target with no authoritative claim
- **THEN** no remote Odoo backup endpoint is called, the shared downstream stages consume the existing file, and completed provenance records `cluster_id=null`/unknown

#### Scenario: Local archive uses the common pipeline

- **WHEN** preparation selects a valid `LocalArchiveRestoreSource`
- **THEN** no remote backup request or backup catalogue insert occurs and the common downstream stages consume a private verified snapshot

#### Scenario: Active managed target records exact identity

- **WHEN** any restore source targets a currently inspected Compose cluster matching an `active` claim
- **THEN** completed provenance records that same non-null `cluster_id` on the exact restore and restored-event rows

#### Scenario: Pending claim blocks every restore source

- **WHEN** the current cluster claim is `pending` for remote, catalogue, or local-archive restore
- **THEN** preparation refuses before database mutation or completed restore audit

#### Scenario: Nullable provenance does not grant destructive authority

- **WHEN** a completed external or legacy restore has `cluster_id=null`/unknown
- **THEN** it remains readable restore history but cannot satisfy any later `db drop` ownership gate

### Requirement: Local restore preflight is mutation-free

Before the first local-restore mutation, preparation SHALL validate the selected source. A catalogue source SHALL have an exact UUID, available state, and matching stable file identity/checksum/format. A local-archive source SHALL resolve to an existing readable non-symlink regular file containing a supported Odoo ZIP with `dump.sql`, valid manifest database identity, and safe bounded filestore members. Both SHALL validate local project PostgreSQL binding, safe target syntax, target absence, and restoration capability while holding the project preparation lock; catalogue restore SHALL additionally hold its backup lifecycle lock. Any failure SHALL create no database and change no project configuration.

For a local archive, command construction SHALL capture file device, inode, size, modification time, SHA-256, validated archive metadata, and a private project-owned snapshot destination without writing it. Execution SHALL re-open the source without following symlinks, require the captured identity, stream it to an exclusive mode-0600 snapshot while recomputing size and SHA-256, and fail before database mutation if any evidence differs. Every restore consumer SHALL read only that snapshot. The snapshot and derived temporary dump/filestore staging artifacts SHALL be removed after success or failure; cleanup SHALL NOT remove the caller's source archive or a confirmed restored database.

#### Scenario: Catalogue checksum differs

- **WHEN** the registered file content no longer matches its catalogue checksum
- **THEN** preparation fails before invoking restore and retains the prior project default

#### Scenario: Invalid local archive fails before mutation

- **WHEN** a local path is missing, unreadable, a symlink, non-regular, invalid ZIP, lacks `dump.sql`, has an incompatible manifest, or contains unsafe or unbounded filestore entries
- **THEN** preparation fails before database creation, filestore creation, restore audit, or project-default change

#### Scenario: Local path changes after selection

- **WHEN** the local archive identity or bytes differ after command construction and before snapshot materialization
- **THEN** execution fails before database mutation and no substituted payload is consumed

#### Scenario: Verified snapshot is the only consumed payload

- **WHEN** local-archive restore passes execution revalidation
- **THEN** database and filestore stages consume only the private verified snapshot and never reopen the caller path

#### Scenario: Dry-run captures evidence without staging

- **WHEN** a valid local archive is selected with `--dry-run`
- **THEN** planning validates and captures its immutable evidence but creates no snapshot, dump, filestore, database, audit row, or project-config change

#### Scenario: Default target generation

- **WHEN** no target is supplied
- **THEN** preparation derives the source database name from the validated archive manifest and selects a safe collision-free new database name without deleting an older database
