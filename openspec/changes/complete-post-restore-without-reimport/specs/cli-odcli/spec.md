## MODIFIED Requirements

### Requirement: Database inventory and registered restore CLI

`odcli db list [--tracked]` SHALL project the read-only project-cluster inventory in all bounded formats. `odcli db restore [BACKUP_UUID] [--file PATH] [--target DATABASE] [--reset-admin-password] [--dry-run] [--yes]` SHALL require exactly one restore source: a complete retained-backup UUID or a local archive path. UUID selection SHALL invoke the existing registered-backup path; file selection SHALL invoke the same public `EnvironmentResource.refresh_database_command()` boundary with a typed local-archive source. `--replace` SHALL remain restricted to a retained backup UUID and SHALL reject `--file`.

`odcli db restore --complete DATABASE [--update-module MODULE ...] [--dry-run] [--yes]` SHALL be a mutually exclusive mode of the same leaf. `--complete` SHALL reject `BACKUP_UUID`, `--file`, `--target`, `--replace`, and `--reset-admin-password`; `--update-module` SHALL be accepted only with `--complete`, SHALL run only explicitly named modules, and SHALL never infer modules or select all modules. Completion SHALL invoke the public typed completion command and SHALL require a proven non-null post-restore checkpoint for the exact project-owned target.

Normal Rich execution SHALL confirm unless `--yes`, machine execution SHALL require `--yes`, and dry-run SHALL not prompt. A successful restore or completion SHALL switch the project default only after all applicable postconditions and administrator reset succeed. Completion output SHALL identify the target, checkpoint, completed stages, pending stage, requested modules, previous/effective default, and publication status through the shared sanitized Rich/JSON/TOON envelope. The existing `db restore` row in `PUBLIC_LEAF_CASES` SHALL remain the only inventory row for this leaf.

#### Scenario: Database list is read-only

- **WHEN** `db list` encounters a PostgreSQL database absent from catalogue provenance
- **THEN** it reports unknown origin and writes no dropped audit event

#### Scenario: Tracked list

- **WHEN** `db list --tracked` runs
- **THEN** it returns only exact cluster/database identities having proven restore or lifecycle relationships

#### Scenario: Restore default target

- **WHEN** `db restore UUID --yes` omits `--target`
- **THEN** it restores to a generated collision-free name, preserves the backup and prior database, and switches default after full success

#### Scenario: File restore dry-run

- **WHEN** `db restore --file BACKUP.zip --target restored_db --dry-run` receives a valid supported Odoo ZIP
- **THEN** it emits the same bounded redacted restore-plan shape as UUID restore, performs no mutation, and does not prompt

#### Scenario: Exactly one source is required

- **WHEN** ordinary `db restore` receives both `BACKUP_UUID` and `--file`, or receives neither without `--complete`
- **THEN** Click exits with status 2 and a clear usage error before project, catalogue, database, filestore, or configuration mutation

#### Scenario: Replacement remains catalogue-only

- **WHEN** `db restore --file BACKUP.zip --replace` is invoked
- **THEN** Click exits with status 2 before environment resolution or mutation

#### Scenario: Restore preflight fails

- **WHEN** the selected UUID or local archive fails identity, readability, checksum, format, cluster binding, or target-name preflight
- **THEN** the command exits 1 with no database or project-config mutation

#### Scenario: Completion dry-run is inspectable and inert

- **WHEN** `db restore --complete restored_db --update-module base --dry-run` targets a proven completion-eligible binding
- **THEN** it emits one redacted plan containing module update, pending reset, checkpoint advance, and default publication as applicable, performs no mutation, and does not prompt

#### Scenario: Completion options are exclusive

- **WHEN** `--complete` is combined with a restore source, `--target`, `--replace`, or `--reset-admin-password`, or `--update-module` is used without `--complete`
- **THEN** Click exits with status 2 before project, catalogue, database, filestore, process, or configuration mutation

#### Scenario: Completion failure is actionable and secret-free

- **WHEN** module update, administrator reset, checkpoint advance, or default publication fails during completion
- **THEN** the failure envelope identifies the exact failed and pending step, retained target, completed stages, requested module names, and unchanged prior default without including secrets, script source, or unsanitized process output

#### Scenario: Completion preserves machine-format parity

- **WHEN** the same completion result or failure is rendered as JSON and TOON
- **THEN** decoding both documents yields the same value, stdout contains one document without ANSI or prompts, and renderer choice does not change execution or exit status
