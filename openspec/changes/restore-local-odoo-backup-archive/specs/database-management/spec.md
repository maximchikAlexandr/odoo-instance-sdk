## ADDED Requirements

### Requirement: Source-neutral provenance for local archive restores

The catalogue schema SHALL represent completed restore provenance independently of backup retention. Existing remote and catalogue restores SHALL retain `source_kind=catalogue`, a non-null `backup_id`, and their current behavior. A local-archive restore SHALL atomically record `source_kind=local_archive`, a null `backup_id`, the validated SHA-256 digest, exact database identity, nullable authoritative `cluster_id`, and verified restore-time `data_directory` on the restore and restored-event rows without storing the source path. Schema constraints SHALL reject incomplete or mixed source evidence. Existing rows SHALL migrate deterministically to catalogue provenance, and rollback SHALL restore the prior schema only when no local-archive provenance row would be lost.

The existing inventory and `db rm` ownership gates SHALL consume source-neutral restore bindings. A local-archive row with an exact active cluster identity and contained project-owned data directory SHALL provide the same database/filestore ownership evidence as a catalogue row. Null, missing, malformed, or mismatched cluster/data-directory evidence SHALL remain readable history but SHALL NOT authorize destructive operations.

#### Scenario: Local archive restore records no backup row

- **WHEN** a local-archive restore completes
- **THEN** one restore row and one restored database event contain `source_kind=local_archive`, null `backup_id`, the captured SHA-256, and no source path, while the backups table is unchanged

#### Scenario: Existing provenance remains compatible

- **WHEN** the migration upgrades existing restore and restored-event rows
- **THEN** each row is classified as catalogue provenance with its original non-null backup UUID and existing inventory behavior is unchanged

#### Scenario: Invalid mixed provenance is rejected

- **WHEN** a write supplies local-archive provenance with a backup UUID, omits its digest, or supplies catalogue provenance without a backup UUID
- **THEN** the transaction fails and neither the restore row nor restored event is committed

#### Scenario: Proven local archive can authorize owned cleanup

- **WHEN** the latest local-archive restore binding matches the active inspected cluster and exact contained project data directory
- **THEN** existing guarded `db rm` ownership checks may authorize the database and filestore cleanup without a catalogue backup row

#### Scenario: Unproven local archive cannot authorize cleanup

- **WHEN** local-archive provenance has null or mismatched cluster identity or an unavailable, external, or unsafe data directory
- **THEN** `db rm` fails closed before session, database, audit, or filestore mutation
