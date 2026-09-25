## MODIFIED Requirements

### Requirement: Database inventory and registered restore CLI

`odcli db list [--tracked]` SHALL project the read-only project-cluster inventory in all bounded formats. `odcli db restore [BACKUP_UUID] [--file PATH] [--target DATABASE] [--reset-admin-password] [--dry-run] [--yes]` SHALL require exactly one restore source: a complete retained-backup UUID or a local archive path. UUID selection SHALL invoke the existing registered-backup path; file selection SHALL invoke the same public `EnvironmentResource.refresh_database_command()` boundary with a typed local-archive source. `--replace` SHALL remain restricted to a retained backup UUID and SHALL reject `--file`. Normal Rich execution SHALL confirm unless `--yes`, machine execution SHALL require `--yes`, and dry-run SHALL not prompt. A successful restore SHALL switch the project default only after all restore postconditions and optional administrator reset succeed. The existing `db restore` row in `PUBLIC_LEAF_CASES` SHALL remain the only inventory row for this leaf.

#### Scenario: Database list is read-only

- **WHEN** `db list` encounters a PostgreSQL database absent from catalogue provenance
- **THEN** it reports unknown origin and writes no dropped audit event

#### Scenario: Tracked list

- **WHEN** `db list --tracked` runs
- **THEN** it returns only exact cluster/database identities having proven restore or lifecycle relationships

#### Scenario: UUID restore default target

- **WHEN** `db restore UUID --yes` omits `--target`
- **THEN** it restores to a generated collision-free name, preserves the backup and prior database, and switches default after full success

#### Scenario: File restore dry-run

- **WHEN** `db restore --file BACKUP.zip --target restored_db --dry-run` receives a valid supported Odoo ZIP
- **THEN** it emits the same bounded redacted restore-plan shape as UUID restore, performs no mutation, and does not prompt

#### Scenario: Exactly one source is required

- **WHEN** `db restore` receives both `BACKUP_UUID` and `--file`, or receives neither
- **THEN** Click exits with status 2 and a clear usage error before project, catalogue, database, filestore, or configuration mutation

#### Scenario: Replacement remains catalogue-only

- **WHEN** `db restore --file BACKUP.zip --replace` is invoked
- **THEN** Click exits with status 2 before environment resolution or mutation

#### Scenario: Restore preflight fails

- **WHEN** the selected UUID or local archive fails identity, readability, checksum, format, cluster binding, or target-name preflight
- **THEN** the command exits 1 with no database or project-config mutation
