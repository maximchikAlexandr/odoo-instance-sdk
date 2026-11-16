# Spec Delta

## Purpose

Provide an explicit local same-cluster PostgreSQL template clone for COPY checkout, with an isolated filestore copy and the existing ownership, dry-run, neutralization, and cleanup contracts.

## ADDED Requirements

### Requirement: Template strategy is explicit and local

COPY checkout SHALL default to database copy strategy `backup`. Strategy `template` SHALL be accepted only when `db_mode` is `copy`, source and target names are both present, neither a named remote nor a catalog backup id is selected, and source and target are proven to belong to one local PostgreSQL cluster. A remote host, a cross-cluster target, or strategy `template` combined with `shared` mode SHALL be rejected before catalogue, filesystem, or database mutation.

#### Scenario: Backup remains the default

- **WHEN** COPY checkout omits the copy strategy
- **THEN** the plan and the recorded environment use strategy `backup` and the existing ZIP restore path

#### Scenario: Template rejects remote or backup selection

- **WHEN** strategy `template` is combined with a named remote or a catalog backup id
- **THEN** checkout fails before any mutation and does not create a database or filestore

#### Scenario: Template requires one local cluster

- **WHEN** strategy `template` cannot prove that the source database exists, the target database is absent, and both names are served by the same local cluster
- **THEN** checkout fails before `CREATE DATABASE` and does not terminate sessions

### Requirement: Template names and paths stay contained

Before any database or filesystem mutation, template checkout SHALL apply the existing COPY name contract to the source and target. Canonical source and target filestore paths SHALL remain under the exact resolved filestore root and SHALL NOT pass through an escaping symlink. SQL identifiers SHALL be quoted by the server from bound variables. The target database SHALL NOT be overwritten.

#### Scenario: Unsafe name is rejected

- **WHEN** a template source or target contains a slash, backslash, NUL, absolute syntax, `.` or `..`, or exceeds 63 UTF-8 bytes
- **THEN** checkout rejects the name before mutation

#### Scenario: Active source sessions block the clone

- **WHEN** the source database has a session other than the maintenance connection
- **THEN** template checkout fails and does not disconnect or terminate that session

### Requirement: Template clone publishes an isolated filestore and a neutralization marker

Template execution SHALL run the captured maintenance statement equivalent to `CREATE DATABASE <target> WITH TEMPLATE <source>` and SHALL verify cluster identity, target existence, and connectivity afterward. The target filestore SHALL be created by copying the source tree into a temporary sibling directory and publishing it with an atomic rename. Source and target SHALL NOT share a writable directory. A missing source filestore SHALL record a warning and SHALL NOT invent files. After the clone, neutralization SHALL run through the local Odoo shell of the creating environment and commit. The environment SHALL become `ready` only when a new read shows `ir.config_parameter` `database.is_neutralized` equal to true. A missing marker, rollback, or failed check SHALL leave the environment on the failed or cleanup path.

#### Scenario: Disposable clone reaches ready

- **WHEN** template checkout clones a disposable source database, copies its filestore, and the neutralization marker is true
- **THEN** the environment is `ready`, `backup_id` is empty, and `target_filestore_path` is the published absolute path

#### Scenario: Missing neutralization marker does not become ready

- **WHEN** the shell exits but the neutralization marker is missing or not true
- **THEN** the environment is not `ready` and only the recorded target database and filestore are eligible for cleanup

#### Scenario: Missing source filestore warns

- **WHEN** the source database exists and its filestore directory does not
- **THEN** checkout continues with a warning and does not create a shared or copied filestore directory

### Requirement: Template plans are immutable and cleanup is recorded

Dry-run SHALL render the same captured maintenance, filesystem, and Odoo-shell plan that execution consumes, and SHALL mutate nothing. Failure and `env remove` SHALL drop only the target database and filestore path recorded for that operation. A path recomputed from the current config SHALL NOT authorize deletion. Source database and source filestore SHALL remain unchanged. Partial cleanup SHALL leave `cleanup_failed` and a repeatable `env remove`. Successful rollback SHALL keep the failed audit event. Public instance construction from an environment SHALL remain limited to `ready` environments. Template rows SHALL NOT be eligible for a backup-scoped filestore pool.

#### Scenario: Dry-run does not mutate

- **WHEN** template checkout is previewed
- **THEN** no database, filestore, catalogue row, or source session changes, and the preview identifies the captured clone plan

#### Scenario: Remove uses the recorded target only

- **WHEN** a template environment is removed or rolled back
- **THEN** only its recorded target database and recorded filestore path are deleted, and the source remains

### Requirement: Catalogue revision 0016 records strategy and path

The catalogue SHALL migrate from revision `0015` to `0016` by adding `db_copy_strategy` and nullable `target_filestore_path` on environments. Existing `copy` rows SHALL be marked `backup` with an empty template path. `shared` rows SHALL keep an empty strategy and empty template path. A `copy` plus `backup` row SHALL keep `target_filestore_path` empty. A `copy` plus `template` row SHALL keep `backup_id` empty and SHALL record the target database and exact filestore path. Downgrade SHALL refuse while any template row exists and SHALL otherwise restore the `0015` shape without dropping backup rows.

#### Scenario: Existing backup rows survive upgrade

- **WHEN** a revision `0015` catalogue with shared and backup COPY rows upgrades to `0016`
- **THEN** shared rows have no strategy or template path, COPY rows are `backup` with an empty template path, and their backup history remains
