## ADDED Requirements

### Requirement: Restore from a registered local backup

CLI-private/internal restore orchestration SHALL accept an exact available catalogue backup UUID and a project PostgreSQL target, perform no remote Odoo backup request or repeat download, and delegate through existing public SDK methods to the existing restore implementation for validation, neutralization, postconditions, and restore audit. It SHALL add no public SDK method. One backup UUID SHALL remain reusable for multiple restores into distinct new database names.

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

### Requirement: Restore failure and interruption retention

Restore failure or Ctrl-C SHALL preserve the source backup and SHALL report the known target database plus whether database creation and atomic project-default switching were confirmed. It SHALL not present a partial restore as success or automatically delete a confirmed database.

#### Scenario: Interrupt during restore

- **WHEN** Ctrl-C occurs after the target name is reserved but before restore completion
- **THEN** the CLI exits 130, keeps the backup, reports the target and known database/default-switch state, and closes the renderer

#### Scenario: Default switch fails

- **WHEN** database restore succeeds but the atomic project configuration switch fails
- **THEN** the operation fails with the restored database reported as retained and the prior default remains authoritative
