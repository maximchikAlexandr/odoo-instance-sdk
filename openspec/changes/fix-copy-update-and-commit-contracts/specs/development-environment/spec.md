## ADDED Requirements

### Requirement: COPY restore uses and verifies the target filestore binding

Ordinary COPY checkout and caller-owned COPY adoption SHALL capture one verified target `data_dir` in the immutable checkout plan. The generated environment configuration and every auxiliary Odoo configuration that can execute `/web/database/backup` or `/web/database/restore` for that checkout SHALL use that captured binding. Source backup SHALL read only `filestore/<source_database>` and target restore SHALL write only `filestore/<target_database>`; neither path SHALL alter source filestore files or stop the running primary Odoo. Existing independent helper-port, backup ownership, recovery, and cleanup contracts SHALL remain in force.

After restore existence and neutralization postconditions, but before the catalogue can record `READY`, checkout SHALL query the target database for every non-empty file-backed `ir.attachment.store_fname` and verify that each normalized value resolves to a regular contained file beneath `<data_dir>/filestore/<target_database>`. A target with no file-backed attachments SHALL satisfy this postcondition. An unreadable query, unsafe path, or missing/non-regular file SHALL fail checkout before finalization and SHALL preserve the existing recovery and borrowed-backup rules.

#### Scenario: Ordinary COPY restores into the environment filestore

- **WHEN** ordinary COPY checkout backs up a source with a file-backed attachment and restores a new target
- **THEN** the helper reads the source file from the captured data root, writes the restored file below the target database directory, leaves the source file unchanged, and records `READY` only after the target file is verified

#### Scenario: Adoption uses the same target binding

- **WHEN** caller-owned COPY adoption restores a retained or named-remote backup
- **THEN** its auxiliary restore server, generated environment configuration, attachment postcondition, and cleanup use the same captured target `data_dir` contract as ordinary checkout

#### Scenario: Missing restored file blocks readiness

- **WHEN** target `ir.attachment` contains a non-empty `store_fname` whose contained regular file is absent after restore
- **THEN** checkout does not record `READY` and follows the existing failed or cleanup-failed recovery path without deleting a borrowed backup

#### Scenario: Database without file-backed attachments is valid

- **WHEN** the restored target contains no `ir.attachment` row with a non-empty `store_fname`
- **THEN** the filestore postcondition succeeds without requiring a fabricated marker file

#### Scenario: Running primary remains available

- **WHEN** COPY requires an auxiliary Database Manager while the primary Odoo is listening on its configured port
- **THEN** checkout uses the existing distinct helper port and does not stop, reconfigure, or replace the primary process
