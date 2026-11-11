## MODIFIED Requirements

### Requirement: Direct and retryable COPY environment removal

Removal of an SDK-owned or caller-owned COPY environment SHALL drop its exact target database through the existing guarded direct PostgreSQL drop operation after revalidating authoritative cluster, database, environment, restore-provenance, journal, and filestore ownership. It SHALL NOT require, start, or contact an Odoo HTTP listener, and SHALL NOT require the recorded HTTP port to be free. The same `env remove` operation SHALL accept `cleanup_failed` as a retryable source state and continue only retained owned cleanup steps. Ownership mismatch, an active owned Odoo runtime, unsafe database sessions, unreadable destructive evidence, or a changed target SHALL fail closed without terminating a process or session implicitly.

A `restore_pending` COPY journal SHALL become removable only when the current target database identity and an exact complete restore row match the journal backup, endpoint, authoritative cluster identity, generated-config `data_dir`, and checkout-owned target filestore. The operation SHALL atomically promote that journal to `restored` before using the existing guarded removal flow. An incomplete, identity-null, foreign, stale, or mismatched restore SHALL remain `restore_pending` and SHALL NOT authorize database, filestore, backup, checkout, or catalog deletion. Repeated reconciliation/removal SHALL be idempotent and SHALL retain borrowed or unknown-ownership backups.

#### Scenario: Stopped COPY environment is removed

- **WHEN** a stopped COPY environment has matching authoritative ownership and no unsafe active database use
- **THEN** `env remove` SHALL directly drop the exact target database, verify its absence, clean only remaining owned artifacts, and record `removed`

#### Scenario: Cleanup retry resumes

- **WHEN** an earlier removal left the environment in `cleanup_failed` with retained owned artifacts
- **THEN** a repeated `env remove` SHALL revalidate current evidence, skip already absent artifacts, and safely continue cleanup

#### Scenario: Complete restore_pending import is reconciled after catalog reopen

- **WHEN** a fresh CLI process removes a `cleanup_failed` COPY environment whose journal is `restore_pending` and whose exact current database OID, complete restore row, backup, endpoint, cluster, generated config, and target `data_dir` all match
- **THEN** the journal SHALL be promoted to `restored` and the existing guarded removal SHALL delete only the exact owned target without reimporting the database or requiring catalog edits

#### Scenario: Incomplete or foreign pending restore remains protected

- **WHEN** a `restore_pending` environment lacks exact complete restore evidence or any database, OID, backup, endpoint, cluster, config, or `data_dir` identity differs
- **THEN** `env remove` SHALL fail before destructive mutation and SHALL retain the journal, database, filestore, backup, checkout, and unrelated resources for diagnosis

#### Scenario: Repeated removal is idempotent

- **WHEN** reconciliation or a prior removal already dropped the exact target or advanced a cleanup stage and the caller repeats `env remove`
- **THEN** removal SHALL skip the proven completed step, continue only remaining owned cleanup, retain borrowed backups, and SHALL NOT affect another database or filestore

#### Scenario: Unrelated port occupant

- **WHEN** an unrelated process occupies the environment's recorded HTTP port but no owned runtime or unsafe database use is proven
- **THEN** removal SHALL neither terminate nor contact that process and the unrelated port alone SHALL NOT block direct database cleanup

#### Scenario: Unsafe active use fails closed

- **WHEN** the exact environment owns a live Odoo runtime or current database sessions cannot be safely attributed and cleared by an existing explicit contract
- **THEN** removal SHALL perform no database drop and SHALL report the blocking evidence

### Requirement: COPY restore uses and verifies the target filestore binding

Ordinary COPY checkout and caller-owned COPY adoption SHALL capture one verified target `data_dir` as `<checkout>/.odcli/filestore` in the immutable checkout plan after the exact checkout path is allocated. The generated environment configuration, every auxiliary Odoo configuration and runtime binding that can execute `/web/database/backup` or `/web/database/restore`, completed restore provenance, attachment verification, and cleanup SHALL use that same captured absolute identity. Source backup SHALL read only `filestore/<source_database>` and target restore SHALL write only `filestore/<target_database>`; neither path SHALL alter source filestore files, the primary project filestore, or a running primary Odoo. Existing independent helper-port, backup ownership, recovery, and cleanup contracts SHALL remain in force.

Before database restore, checkout SHALL validate the selected ZIP through the existing source-neutral archive boundary, prove no-follow containment of the exact target filestore, and materialize the validated source baseline beneath `<data_dir>/filestore/<target_database>`. The exact target SHALL be absent before materialization. Archive traversal, symlinks, changed metadata/content, insufficient space, a non-ZIP COPY source, or an existing/unsafe target SHALL fail before database import and SHALL preserve source and unrelated resources.

After the restore transport succeeds, checkout SHALL verify target existence/OID and an exact complete restore row matching the journal's database, backup, endpoint, cluster, and captured `data_dir`. It SHALL then record journal stage `restored` before later attachment postconditions; environment `ready` SHALL remain forbidden until those postconditions and finalization succeed. Missing or mismatched complete evidence SHALL leave `restore_pending` and SHALL NOT grant cleanup ownership.

Before `ready`, checkout SHALL query the target database for every non-empty file-backed `ir.attachment.store_fname`, bounded by the existing safety limit, normalize each safe relative path, and compare it with the validated source archive member set and exact target file. A regular contained target file SHALL pass. A file present in the source baseline but absent, symlinked, or non-regular in the target SHALL fail closed as target loss. A path absent from both source baseline and target SHALL produce stable warning `copy_source_blob_missing`, SHALL be retained in the typed result and successful lifecycle evidence, and SHALL NOT be reported as COPY-created data loss. Unsafe/undecodable paths and failed/over-limit queries SHALL always fail closed. No missing blob SHALL be synthesized.

#### Scenario: Ordinary COPY restores into the checkout filestore

- **WHEN** ordinary COPY checkout restores a ZIP with a file-backed attachment
- **THEN** the validated baseline and restored file SHALL exist beneath the task checkout's captured target database root, restore provenance and generated config SHALL name that same `data_dir`, the task Odoo SHALL read the file there, and the source and primary project filestores SHALL remain unchanged

#### Scenario: Adoption uses the same target binding

- **WHEN** caller-owned COPY adoption restores a retained or named-remote backup
- **THEN** its checkout-local managed path, auxiliary restore server, generated environment configuration, restore provenance, attachment postcondition, and cleanup SHALL use the same captured target `data_dir` without granting ownership of the caller's code or sibling filestores

#### Scenario: Source-missing blob is reported without false target loss

- **WHEN** target `ir.attachment` references a safe `store_fname` absent from both the validated source ZIP baseline and the exact target filestore
- **THEN** checkout SHALL return warning `copy_source_blob_missing`, retain a sanitized successful lifecycle finding, and MAY reach `ready` without claiming COPY lost a blob that the source did not contain

#### Scenario: Target-only missing blob blocks readiness

- **WHEN** the validated source ZIP contains an attachment member referenced by the target database but the exact target file is absent, symlinked, or non-regular after restore
- **THEN** checkout SHALL not record `ready`, SHALL report target filestore loss, and SHALL retain or clean resources only through the proven journal/ownership path without deleting a borrowed backup

#### Scenario: Complete import survives later verification failure

- **WHEN** database restore, exact OID observation, and complete matching restore provenance succeed but attachment verification or a later postcondition fails
- **THEN** the journal SHALL remain `restored`, the environment SHALL remain non-ready, and immediate compensation or repeated public `env remove` after catalog reopen SHALL be able to clean only the exact owned target without reimporting or manual catalog edits

#### Scenario: Mismatched restore identity stays unresolved

- **WHEN** completed provenance names another `data_dir`, database OID, backup, endpoint, or cluster
- **THEN** the journal SHALL remain `restore_pending`, checkout SHALL not record `ready`, and public cleanup SHALL fail before any destructive mutation

#### Scenario: Database without file-backed attachments is valid

- **WHEN** the restored target contains no `ir.attachment` row with a non-empty `store_fname`
- **THEN** the filestore postcondition SHALL succeed without requiring a fabricated marker file

#### Scenario: Running primary remains available

- **WHEN** COPY requires an auxiliary Database Manager while the primary Odoo is listening on its configured port
- **THEN** checkout SHALL use the existing distinct helper port and SHALL NOT stop, reconfigure, or replace the primary process
