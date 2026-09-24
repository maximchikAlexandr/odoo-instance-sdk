## ADDED Requirements

### Requirement: Doctor MAY surface missing data_directory in a restore binding

`odcli doctor` MAY emit an existing-style finding when a restore binding exists without a `data_directory`. Doctor SHALL NOT auto-assign ownership, SHALL NOT delete filestore by database name, and this change SHALL NOT add a doctor subsystem.

#### Scenario: doctor does not auto-claim unknown paths

- **WHEN** doctor encounters an unknown existing filestore path without a proven binding
- **THEN** it does not assign ownership and does not delete the path

## MODIFIED Requirements

### Requirement: Restore from a registered local backup

Restore SHALL record `data_dir` `{project_root}/.odcli/filestore` from the effective self-contained config in the restore binding alongside cluster/database/backup identity. Before restore, that path SHALL be verified to be a regular directory inside the project tree and not a symlink. `db rm` SHALL delete only the exact contained non-symlink filestore and SHALL return `deleted` or `absent` with the path. External config without a provable `data_dir` SHALL stay fail-closed `unknown`; no directory SHALL be deleted by database name or platform default.

For a self-contained project, auxiliary restore SHALL use bootstrap database `tmp` and SHALL NOT add hidden `--database=__odcli_restore__` or `--db-filter=^$`. After a successful restore, the project default SHALL switch to the restored database; `tmp` SHALL remain the bootstrap database and SHALL NOT be treated as a working backup copy. The regression flow (fresh owned Compose cluster → `tmp` init → stopped-project `db restore` → Database Manager `303` → PostgreSQL postcondition → default DB switch) SHALL be covered for Odoo 13 and Odoo 19.

#### Scenario: Registered backup is restored

- **WHEN** an available backup UUID passes identity, readability, checksum, format, cluster, and target-name preflight
- **THEN** the existing restore path creates the new database, confirms its postcondition, and records provenance to that UUID
- **AND** the source archive remains available

#### Scenario: Backup cannot be used

- **WHEN** the UUID is unknown, not available, missing, unreadable, changed, corrupt, or unsupported
- **THEN** restore fails before database creation and before project default configuration changes

#### Scenario: Target already exists

- **WHEN** the requested exact target exists in the selected cluster
- **THEN** restore refuses and does not overwrite, drop, rename, or select another database

#### Scenario: Same UUID is restored twice

- **WHEN** two invocations use one UUID with two free target names
- **THEN** both restore audit records retain the same backup UUID and distinct database identities

#### Scenario: Public restore methods remain canonical

- **WHEN** local UUID restore orchestration is added and public methods are characterized
- **THEN** `test_discovered_public_methods` reports the unchanged canonical method set

#### Scenario: restore records data_dir

- **WHEN** a self-contained restore completes
- **THEN** the restore binding contains the canonical project-owned `data_dir` together with cluster/database/backup identity

#### Scenario: db rm deletes the proven filestore

- **WHEN** `odcli db rm <DB> --force-connections --yes` runs after a self-contained restore
- **THEN** the exact contained non-symlink filestore is deleted and the result reports `deleted` or `absent` with the path

#### Scenario: external config without data_dir stays unknown

- **WHEN** the effective config has no provable `data_dir`
- **THEN** `db rm` returns `filestore_state = unknown` and no directory is deleted by database name or platform default

#### Scenario: auxiliary restore uses tmp

- **WHEN** a self-contained project runs `db restore`
- **THEN** auxiliary restore uses `tmp` and does not add hidden `--database=__odcli_restore__` or `--db-filter=^$`

#### Scenario: restore switches default and keeps tmp as bootstrap

- **WHEN** a self-contained restore completes successfully
- **THEN** the project default switches to the restored database and `tmp` remains the bootstrap database

#### Scenario: regression flow for Odoo 13 and Odoo 19

- **WHEN** the regression flow runs (fresh owned Compose cluster → `tmp` init → stopped-project `db restore` → Database Manager `303` → PostgreSQL postcondition → default DB switch)
- **THEN** each step succeeds for Odoo 13 and Odoo 19

### Requirement: Mutating DB methods require password at call time

`backup()`, `restore()`, and `drop()` SHALL still require the Odoo master password at call time and SHALL raise `MasterPasswordRequiredError` when it is absent, exactly as in the existing contract. The admin password reset is a separate, explicit secret assignment, distinct from the master password, using the same rules as `odcli db reset-admin-password` in `cli-odcli` (RICH `getpass` twice; otherwise `ODCLI_ADMIN_PASSWORD`; `--dry-run` does not prompt; missing secret is `admin_password_required`). After a successful reset, login as `base.user_admin` SHALL succeed on Odoo 13 and Odoo 19 via the real-Odoo XML-RPC test-support probe.

#### Scenario: Backup without password

- **WHEN** `instance.databases.backup()` и `master_password is None`
- **THEN** `MasterPasswordRequiredError` raised before HTTP request

#### Scenario: Restore without password

- **WHEN** `instance.databases.restore(backup, "target")` и `master_password is None`
- **THEN** `MasterPasswordRequiredError` raised before HTTP request

#### Scenario: List without password

- **WHEN** `instance.databases.list()` и `master_password is None`
- **THEN** list succeeds (no password needed for read)

#### Scenario: interactive reset prompts hidden

- **WHEN** a restore with `--reset-admin-password` runs with RICH output and without `--no-input`
- **THEN** the new password is prompted hidden with confirmation and is not echoed

#### Scenario: dry-run does not prompt

- **WHEN** a restore with `--reset-admin-password --dry-run` runs
- **THEN** no prompt occurs and no mutation occurs

#### Scenario: non-interactive reset without secret fails before mutation

- **WHEN** a restore with `--reset-admin-password --yes` runs without `ODCLI_ADMIN_PASSWORD` and without a RICH prompt path
- **THEN** it fails with `admin_password_required` before any restore/drop mutation

#### Scenario: secret provenance is reported

- **WHEN** a reset succeeds
- **THEN** the result reports the fact and provenance (`prompt` or `environment`) and the secret is absent from all outputs

#### Scenario: no implicit admin fallback

- **WHEN** a reset runs without a provided secret
- **THEN** no implicit `admin` or other common value is assigned

#### Scenario: admin login works after reset

- **WHEN** restore or `db reset-admin-password` succeeds on Odoo 13 or Odoo 19
- **THEN** XML-RPC login as `base.user_admin` with the assigned secret succeeds
