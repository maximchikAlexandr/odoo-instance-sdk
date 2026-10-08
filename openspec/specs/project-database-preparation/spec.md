# project-database-preparation Specification

## Purpose
The private workflow for safely preparing a project database from a configured test instance.
## Requirements
### Requirement: One project database preparation workflow

The SDK SHALL implement one private concrete project-database preparation workflow used by manual refresh and checkout freshness. The workflow SHALL accept typed options and return a typed result through the existing `EnvironmentResource`; it SHALL compose the existing `DatabaseResource`, `BackupCatalog`, `PostgresCluster`, manifest writer, lock primitive, and Odoo shell. CLI callbacks and checkout SHALL NOT duplicate download/restore/reset/default-switch orchestration.

The workflow SHALL support download-only and download-plus-restore. Admin reset SHALL be an optional post-restore step. It SHALL NOT create a `DatabaseService`, workflow engine, scheduler, provider interface, second downloader, or second catalog.

#### Scenario: Manual and checkout use the same workflow

- **WHEN** a manual refresh and a stale checkout each require a remote test backup
- **THEN** both invoke the same preparation workflow with different typed options and receive the same typed result shape

#### Scenario: Download-only stops after audit

- **WHEN** preparation runs with `restore=False`
- **THEN** it downloads and catalogs a backup but creates no local database and does not change the project default

### Requirement: Restore preflight and collision-free target

For `restore=True`, the workflow SHALL perform all non-mutating local rejection checks before starting the remote download: resolve the project and source config, assert the target Odoo endpoint is local, require its local master password, verify a ready local runtime/database-manager path, and require `PostgresCluster.ensure_running()` to complete successfully. Download-only SHALL NOT require local runtime or PostgreSQL readiness.

The workflow SHALL generate a new PostgreSQL-safe target name from the remote database, a UTC timestamp, and a collision-resistant suffix, without inserting a literal refresh marker. It SHALL validate the name with the existing database-name rules, remain within PostgreSQL's 63-byte identifier limit, and recheck `DatabaseResource.exists()` under the project preparation lock. It SHALL never drop, overwrite, or reuse an existing database.

#### Scenario: Restore preflight fails before download

- **WHEN** `restore=True` and the project PostgreSQL cluster cannot become healthy
- **THEN** no remote backup request starts and the project default remains unchanged

#### Scenario: Generated name collides

- **WHEN** a generated target name already exists
- **THEN** the workflow generates/rechecks another safe name and never drops or overwrites the existing database

#### Scenario: Generated name omits refresh marker

- **WHEN** the workflow automatically generates a restore target for source database `source` at timestamp `20260903090420` with suffix `2ee3a458a068`
- **THEN** the target is `source_20260903090420_2ee3a458a068` and does not contain `_refresh_`

### Requirement: Restore, optional admin reset, and atomic default switch

After download, `restore=True` SHALL call the existing `DatabaseResource.restore(backup, target, copy=True, neutralize_database=True)`. The successful restore SHALL retain the existing `restores.backup_id` mapping. If requested, admin reset SHALL run only after restore succeeds. The workflow SHALL atomically replace `ProjectConfig.default_source_database` only after restore and every requested post-restore step succeed.

The manifest update SHALL re-read the project manifest under the preparation lock, preserve unrelated fields, use the existing secret-checked atomic writer, and refuse to overwrite conflicting preparation configuration changed since planning. Download-only SHALL never update the default.

#### Scenario: Full preparation succeeds

- **WHEN** refresh downloads, restores, and successfully resets the administrator
- **THEN** the backup and restore mapping remain, and the project default atomically changes to the new target database

#### Scenario: Admin reset fails after restore

- **WHEN** restore succeeds but the requested administrator reset fails
- **THEN** the restored database and restore mapping are retained, the prior project default remains byte-for-byte effective, and the error/result identifies the retained target without a password

#### Scenario: Manifest switch fails

- **WHEN** all database steps succeed but the atomic manifest switch fails or detects a conflicting edit
- **THEN** the new database and mapping remain as retained artifacts and the old project default remains effective

### Requirement: Local administrator reset through the ORM

`DatabaseResource.reset_admin_password()` SHALL accept no database argument and SHALL reset the administrator only when its local `OdooInstance` is bound to exactly one configured database. It SHALL resolve the administrator by XML ID `base.user_admin`, set its `password` field to the fixed value `admin` through the Odoo ORM, and commit through the existing Odoo shell execution path. Direct SQL, manual password hashing, login-name lookup, numeric-ID lookup, and automatic reset during checkout are forbidden.

For refresh, preflight SHALL resolve one project runtime binding containing the exact Python/Odoo executable prefix, runtime cwd, and ready project `PostgresCluster`. The workflow SHALL derive a mode-`0600` ephemeral config from the validated local source config, set `db_name` and `dbfilter` only to the restored target, and use a private target-instance helper to combine its `StartConfig`/database connection with that runtime binding and a canonical exclusive artifact lock keyed by project and target database. It SHALL remove the config after the shell exits. It SHALL NOT use bare `client.instance.from_config()` for reset. Standalone reset SHALL use `client.instance.from_environment()` for exactly one resolved ready environment after verifying its generated config's single database equals the environment's recorded target/source database. The reset result SHALL report the bound database, optional standalone environment ID, XML ID, and success state. The fixed password SHALL not appear in output, structured envelopes, exceptions, argv, or logs.

#### Scenario: Context-selected reset

- **WHEN** reset runs in a ready registered worktree whose generated config binds exactly one local database
- **THEN** `env.ref('base.user_admin')` is updated through ORM, the shell transaction commits, and no password is emitted

#### Scenario: Refresh binds the restored target

- **WHEN** refresh restores `source_refresh_123` while the project source config still names `source`
- **THEN** reset runs through an ephemeral instance whose only configured database is `source_refresh_123`, the source config remains unchanged, and the ephemeral config is removed

#### Scenario: Refresh reset uses the project runtime and lock

- **WHEN** refresh resets its restored target
- **THEN** the shell uses the preflighted Python/Odoo prefix and runtime cwd, rechecks the bound project PostgreSQL cluster, and holds the canonical target artifact lock exclusively for the committed ORM script

#### Scenario: Ambiguous or remote reset rejected

- **WHEN** no single bound database can be proven or the selected instance is non-local
- **THEN** reset fails before running the shell and does not modify any user

### Requirement: Project-scoped serialization and freshness recheck

One canonical project preparation lock SHALL serialize manual refresh, restore, and checkout-triggered preparation for the same canonical Git project. After acquiring the lock, every caller SHALL re-read the manifest, latest restore/backup mapping, backup file/state, and freshness before deciding whether work is still required. A waiter SHALL reuse a qualifying result produced by the preceding caller rather than download or restore again.

Freshness SHALL be evaluated only by checkout and only when `refresh_after_hours` is configured. A current default is stale when it has no available mapped backup, the mapped file is missing, or `downloaded_at + refresh_after_hours` is not later than the current UTC time. Manual refresh SHALL always execute when requested and no background timer, daemon, or scheduler SHALL be added.

#### Scenario: Concurrent stale checkouts coalesce

- **WHEN** two checkouts concurrently observe a stale project default
- **THEN** one prepares the database while holding the project lock and the second rechecks after locking and reuses the fresh default

#### Scenario: Freshness disabled

- **WHEN** checkout runs with no `refresh_after_hours` configured
- **THEN** no age-based refresh occurs, while provenance validation still runs

### Requirement: Failure retention and sanitized audit

Once a backup download succeeds, later workflow failures SHALL NOT delete it. Once a restore is confirmed, later failures SHALL NOT drop the database or remove its restore mapping. The workflow SHALL preserve the prior project default until full requested success and SHALL return or raise enough sanitized information to identify retained backup/database artifacts for manual recovery.

No failure path SHALL print or persist either remote or local master passwords, the fixed administrator password, environment contents, multipart bodies, or complete config files.

#### Scenario: Failure after database creation

- **WHEN** an injected failure occurs after the target database is confirmed but before the default switch
- **THEN** the target and mapping remain, the old default remains active, and sanitized output names the retained target and backup ID

### Requirement: Inspectable database preparation command

Project database preparation/refresh SHALL expose one command whose plan captures every existing psql, pg_restore, Odoo shell, and other child-process step before the first mutation, alongside honest action steps for HTTP/database/filesystem/catalog/lock work. Existing coordinator serialization, coalescing, retained-artifact reporting, and atomic default switching SHALL remain operation-specific.

#### Scenario: Refresh dry-run is inspected

- **WHEN** a caller builds a refresh command with restore and administrator reset enabled
- **THEN** its plan shows the captured restore and Odoo shell steps, commit/rollback intent, and relevant action steps
- **AND** no backup, database, config, catalog, or process mutation occurs

#### Scenario: Preparation fails after mutation

- **WHEN** execution fails after a database or retained backup has been created
- **THEN** the existing typed failure context and explicit compensation/retention rules apply
- **AND** failure is not hidden in a generic pipeline result

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

#### Scenario: Pending claim blocks both restore sources

- **WHEN** the current cluster claim is `pending` for remote, catalogue, or local-archive restore
- **THEN** preparation refuses before database mutation or completed restore audit

#### Scenario: Nullable provenance does not grant destructive authority

- **WHEN** a completed external or legacy restore has `cluster_id=null`/unknown
- **THEN** it remains readable restore history but cannot satisfy any later `db drop` ownership gate

### Requirement: Local restore preflight is mutation-free

Before the first local-restore mutation, preparation SHALL validate the selected source. A catalogue source SHALL have an exact UUID, available state, and matching stable file identity/checksum/format. A local-archive source SHALL resolve to an existing readable non-symlink regular file containing a supported Odoo ZIP with `dump.sql`, valid manifest database identity, and safe bounded filestore members. Both SHALL validate local project PostgreSQL binding, safe target syntax, target absence, and restoration capability while holding the project preparation lock; catalogue restore SHALL additionally hold its backup lifecycle lock. Any failure SHALL create no database and change no project configuration.

For a local archive, command construction SHALL capture file device, inode, size, modification time, SHA-256, validated archive metadata, and a private project-owned snapshot destination without writing it. Execution SHALL re-open the source without following symlinks, require the captured identity, stream it to an exclusive mode-0600 snapshot while recomputing size and SHA-256, and fail before database mutation if any evidence differs. Every restore consumer SHALL read only that snapshot. The snapshot and derived temporary dump/filestore staging artifacts SHALL be removed after success or failure; cleanup SHALL NOT remove the caller's source archive or a confirmed restored database.

#### Scenario: Preflight checksum differs

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

### Requirement: Preparation exposes truthful long-step progress

Remote wait/download, validation, restore, neutralization, administrator reset, postcondition, and default switch SHALL expose their applicable planned step lifecycle through the shared observer. Completion of each logical action SHALL be emitted only after that action's effect and postcondition.

#### Scenario: Restore postcondition fails

- **WHEN** the restore request returns but the target database cannot be confirmed
- **THEN** the restore step is reported failed rather than completed and default switching does not start

#### Scenario: Dry-run preparation

- **WHEN** either source variant is previewed
- **THEN** the immutable plan identifies the selected source and target but no runtime progress or effect occurs

### Requirement: Preparation records canonical project_id on download

The preparation workflow SHALL pass the resolved canonical `project_id` into `start_download()` before HTTP transfer for both download-only refresh and remote-backup plus local-restore flows. The backup row SHALL record the canonical `project_id` so the result is visible in the project's `backup ls` without an environment or restore link. Generic SDK backup without project context SHALL remain unowned.

#### Scenario: Refresh records project_id

- **WHEN** `odcli db refresh` downloads a remote backup for a resolved project
- **THEN** the resulting backup row has a non-null canonical `project_id`

#### Scenario: Remote restore preparation preserves ownership

- **WHEN** remote-backup plus local-restore runs for a resolved project
- **THEN** the downloaded backup row records the canonical `project_id` before the local restore begins

### Requirement: Trusted project source and password selection

The workflow SHALL resolve the remote URL/database from the explicitly selected named entry, or from legacy `[test_instance]` when no name is supplied. Named-only projects without a selector SHALL fail; unknown names SHALL never fall back. Legacy preparation SHALL read `ODCLI_TEST_MASTER_PASSWORD`; named preparation SHALL read the source-specific password described below. Passwords SHALL be passed only to the selected remote instance and SHALL NOT appear in manifest, catalog, results, exceptions, argv, logs or fingerprints. Missing configuration or missing/empty secrets SHALL fail before network or local mutation.

Project-configured remote URLs SHALL be trusted for named and legacy sources. Separate origin approval SHALL NOT be required. `ODCLI_TEST_INSTANCE_ORIGIN_PINS` and `ODCLI_REMOTE_<NAME>_ORIGIN` SHALL be treated as legacy no-op variables: their absence, emptiness, mismatched or malformed values SHALL NOT affect source selection or block execution. Init and diagnostics SHALL NOT generate or request them. Existing dotenv files SHALL NOT be rewritten to remove them; normal dotenv syntax validation remains applicable.

URL normalization and validation, TLS verification and the existing once-per-process cleartext-secret warning for non-loopback HTTP SHALL remain. Automatic redirects SHALL remain disabled; any redirect response SHALL fail without replaying a password-bearing request. Generic direct backup calls SHALL retain their current contract.

#### Scenario: Configured source needs no origin approval

- **WHEN** a named or legacy project source has a valid URL and required password but no origin variable
- **THEN** preparation proceeds without a separate origin approval check

#### Scenario: Legacy origin values are ignored

- **WHEN** old origin variables are present with empty, mismatched or invalid-origin values in otherwise valid dotenv or process environment
- **THEN** the SDK uses the project URL and selected password exactly as if those variables were absent

#### Scenario: Configured HTTP retains its warning

- **WHEN** a trusted project source uses non-loopback HTTP
- **THEN** preparation emits the existing cleartext-secret warning before sending the password and keeps the password redacted

`source_branch` in typed options SHALL override `test_instance.git_branch`. The branch value is declarative provenance; the SDK SHALL NOT query or infer Git state from the remote Odoo instance. The result SHALL identify the branch origin as `explicit`, `configured`, or `unknown` without exposing secrets.

#### Scenario: Explicit branch override

- **WHEN** `[test_instance].git_branch="develop"` and refresh supplies `source_branch="release/19"`
- **THEN** the backup records `release/19` and the result reports branch origin `explicit`

#### Scenario: Missing remote password

- **WHEN** legacy refresh is requested without a non-empty `ODCLI_TEST_MASTER_PASSWORD`
- **THEN** it fails before HTTP, catalog, PostgreSQL, or manifest mutation and no secret value appears in the error

For a named source, the SDK SHALL derive only `ODCLI_REMOTE_<UPPER_NAME>_MASTER_PASSWORD`. Process environment SHALL override the existing owner-only project dotenv per key; worktrees SHALL resolve the registered project root. An empty process override SHALL fail. The selected project URL SHALL be trusted without a separate origin approval variable. URL validation, TLS verification, existing transport warnings and canonicalization SHALL apply. Redirect responses SHALL fail without follow-up requests. Every key matching a valid named-source password form SHALL be stripped from all child-process environments, including local Odoo, and all diagnostic projections.

Named-source branch provenance SHALL come from its configured branch or explicit override using the same declared/unknown semantics as legacy preparation. Results and backup catalog provenance SHALL retain nullable historical source name, normalized origin, database and declared branch; configuration edits or removal SHALL NOT rewrite historical provenance.

#### Scenario: Two passwords remain separate

- **WHEN** lab and staging are configured with different credentials
- **THEN** a staging request uses only the staging password, and neither password reaches a local child process or output

#### Scenario: Source URL changed before planning

- **WHEN** a new command is planned after the selected project URL changes to another valid URL
- **THEN** it uses that configured URL without consulting legacy origin variables
- **AND** changing the profile after planning still causes the existing stale-plan rejection

#### Scenario: Cross-origin redirect

- **WHEN** a configured endpoint redirects to another origin
- **THEN** no password-bearing request is forwarded to that other origin

#### Scenario: Worktree and process override

- **WHEN** preparation runs from a registered worktree and a staging password is present in both project dotenv and process environment
- **THEN** the process value wins, the same canonical project dotenv is used, and an empty override is rejected

#### Scenario: Named source omitted or misspelled

- **WHEN** a named-only project omits the selector or a caller supplies an unknown name
- **THEN** preparation fails with available names and does not select the first profile

### Requirement: Named-source preparation reuses existing behavior

An explicit named source SHALL use the current download, validation, local restore, neutralization, audit and failure-retention behavior. Manual refresh with restore SHALL retain its existing atomic default switch; download-only SHALL leave the default unchanged. Freshness/coalescing SHALL compare canonical project, source origin, database and branch, not merely source name or database name. Captured profile changes SHALL cause a stale-plan failure before mutation.

#### Scenario: Refresh staging

- **WHEN** a caller selects staging for manual refresh with restore
- **THEN** it downloads staging, creates a new local target through existing preparation, and switches the project default only after all requested postconditions succeed

#### Scenario: Lab result cannot satisfy staging

- **WHEN** concurrent requests select lab and staging with an identically named remote database
- **THEN** a completed lab result is not reused as the staging result

### Requirement: Diagnostics explain source readiness without hidden backup

Existing public diagnostics SHALL accept an optional named source and report configuration, password presence, Git ref and local restore prerequisites without exposing values. Offline diagnostics SHALL make no network request. Checks SHALL NOT create a backup just to test credentials; authentication SHALL be reported unverified until actually demonstrated.

#### Scenario: Missing staging secret

- **WHEN** source diagnostics find a missing password
- **THEN** they report the missing variable name and corrective action without downloading data or exposing another secret

### Requirement: Failed restore retains exact partial-target recovery evidence

Database preparation SHALL use its captured post-restore existence probe after a restore exception. WHEN that probe proves the preselected target exists in the active SDK-owned PostgreSQL cluster, THEN the catalogue SHALL persist a secret-free `incomplete` restore binding containing the exact cluster, endpoint, database, and captured catalogue-backup or local-archive provenance. The failure result SHALL identify the retained target and incomplete state. WHEN the probe fails, is unavailable, or does not prove the exact target in the active cluster, THEN no database ownership SHALL be inferred or recorded. An incomplete target SHALL NOT become the project default or satisfy a successful/coalesced restore result.

#### Scenario: Restore fails after target creation

- **WHEN** restore raises after PostgreSQL creates the selected target and the captured post-failure probe proves that target in the active owned cluster
- **THEN** preparation fails while publishing one exact incomplete binding and reports the retained database without switching the project default

#### Scenario: Target existence cannot be proved

- **WHEN** restore fails and the captured probe cannot prove the selected target in the active owned cluster
- **THEN** preparation keeps the original failure primary, reports the database as unconfirmed, and creates no recovery binding

#### Scenario: Retry an incomplete target

- **WHEN** a later supported restore retry selects the exact incomplete binding and its source and cluster evidence still match
- **THEN** preparation SHALL safely reconcile or replace that target through the existing collision and ownership gates and SHALL NOT treat it as a completed restore before all postconditions pass

### Requirement: Successful default switch synchronizes owned runtime config

WHEN a restore completes all requested postconditions and switches `default_source_database`, database preparation SHALL update the project manifest and the project-owned generated Odoo config under the existing preparation lock before returning success. The generated `db_name` and `dbfilter` SHALL select the new default while preserving all unrelated generated settings. A user-managed source config SHALL remain byte-for-byte unchanged. WHEN either owned write fails, THEN preparation SHALL report failure, compensate any preceding ordinary write from owner-validated captured content, and SHALL NOT emit `default_switched=true`.

#### Scenario: Successful restore switches effective database

- **WHEN** restore of `staging_restored` completes and default switching is enabled for a project using `.odcli/odoo.conf`
- **THEN** both `project.toml` and the generated config select `staging_restored` before success and the next `odcli run` uses that database and filter

#### Scenario: User-managed config is preserved

- **WHEN** preparation switches the default for a project whose effective source config is not project-owned generated config
- **THEN** the manifest is updated and the external source config remains byte-for-byte unchanged

#### Scenario: Generated config write fails

- **WHEN** the generated-config update fails during default switching
- **THEN** preparation restores the prior owned file state where an ordinary exception permits compensation, reports failure, and does not claim that the default switch completed

