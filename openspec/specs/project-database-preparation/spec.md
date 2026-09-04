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

### Requirement: Configured remote test source and secret boundary

The workflow SHALL resolve the remote base URL and database only from `[test_instance]` in the project manifest. The remote database master password SHALL be read only from `ODCLI_TEST_MASTER_PASSWORD` at execution time, SHALL be passed only to the remote `OdooInstance`, and SHALL NOT be persisted in the manifest, catalog, result, exception, argv, or logs. Missing configuration or a missing/empty environment secret SHALL fail before network or local mutation.

Repository-selected preparation flows SHALL require an external exact-origin approval for every non-loopback test-instance origin before preparation work begins. `ODCLI_TEST_INSTANCE_ORIGIN_PINS` SHALL be the sole approval channel and SHALL contain one comma-separated list of non-secret exact origins. Entries SHALL be compared after the existing canonical-origin normalization (lowercase scheme/host and effective port); paths, queries, fragments, wildcards, and host-only values SHALL not broaden approval. Loopback origins SHALL not require a pin. A non-loopback HTTP origin SHALL be permitted only when its canonical exact origin is pinned and SHALL emit the existing once-per-process cleartext-secret warning before sending a master password. Transport and approval SHALL be checked before the preparation lock, PostgreSQL readiness, database-manager access, HTTP, catalog mutation, or manifest mutation. The approval variable SHALL not be persisted. Generic direct `DatabaseResource.backup()` calls SHALL retain their existing contract and SHALL not require this repository-selected approval.

#### Scenario: Operator approves a repository-selected origin

- **WHEN** the operator exports `ODCLI_TEST_INSTANCE_ORIGIN_PINS="https://odoo-test.example:443"` and runs project refresh against `[test_instance].base_url = "https://odoo-test.example"`
- **THEN** the canonical exact-origin check accepts the remote source without exposing or persisting a password; an unpinned or differently ported origin fails before preparation mutation

#### Scenario: Operator approves a repository-selected HTTP origin

- **WHEN** the operator pins the exact canonical origin `http://odoo-test.example:8069` and runs project refresh against that HTTP test instance
- **THEN** preparation is allowed, the cleartext-secret warning is emitted before the password-bearing request, and the password remains redacted from every output and persisted artifact

#### Scenario: Unpinned HTTP origin remains rejected

- **WHEN** a repository selects a non-loopback HTTP origin that is absent from `ODCLI_TEST_INSTANCE_ORIGIN_PINS`
- **THEN** preparation fails before network or local mutation

`source_branch` in typed options SHALL override `test_instance.git_branch`. The branch value is declarative provenance; the SDK SHALL NOT query or infer Git state from the remote Odoo instance. The result SHALL identify the branch origin as `explicit`, `configured`, or `unknown` without exposing secrets.

#### Scenario: Explicit branch override

- **WHEN** `[test_instance].git_branch="develop"` and refresh supplies `source_branch="release/19"`
- **THEN** the backup records `release/19` and the result reports branch origin `explicit`

#### Scenario: Missing remote password

- **WHEN** refresh is requested without a non-empty `ODCLI_TEST_MASTER_PASSWORD`
- **THEN** it fails before HTTP, catalog, PostgreSQL, or manifest mutation and no secret value appears in the error

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

