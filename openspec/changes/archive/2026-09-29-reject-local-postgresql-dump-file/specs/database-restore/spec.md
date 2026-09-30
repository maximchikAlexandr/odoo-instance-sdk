## MODIFIED Requirements

### Requirement: Restore from a caller-owned local Odoo ZIP

The environment restore command SHALL accept public frozen typed `LocalArchiveRestoreSource(path: str)` through its existing restore-source parameter. It SHALL support Odoo ZIP archives containing `dump.sql`, a compatible manifest database name, and safe filestore content. It SHALL preserve the caller-owned archive, restore into a new target, verify database and filestore postconditions, apply the existing neutralization and optional administrator reset, switch the project default only after full success, and return `DatabasePreparationResult` without fabricating a `Backup`. It SHALL NOT persist the source path, expose it in plan/output/error projections, add a public restore method, or support native dump/remote URL/format conversion through this source.

Before ZIP validation, the local-file preflight SHALL classify a bounded content prefix captured through the same no-follow descriptor and stable file-identity check used to hash the caller-owned file. A `PGDMP` prefix SHALL identify a PostgreSQL custom-format dump without invoking `pg_restore`; a recognized ZIP-family prefix SHALL continue through existing bounded ZIP validation; all other content SHALL be unknown. Classification SHALL NOT depend on the filename or extension and SHALL NOT read secret or unbounded content.

A recognized PostgreSQL custom dump SHALL fail with typed code `backup_unsupported_format` and sanitized message `unsupported local backup format: PostgreSQL custom dump`. Unknown content SHALL fail with typed code `backup_unknown_format` and sanitized message `unrecognized local backup format`. A recognized but malformed ZIP SHALL retain typed code `backup_corrupt` and the existing sanitized corrupt-archive diagnostic. These failures SHALL occur before snapshot creation, subprocess execution, database mutation, catalogue write, or project-default change, and SHALL NOT expose the caller-owned path or content. The existing catalogued `BackupFormat.DUMP` validation and restore branch SHALL remain unchanged.

#### Scenario: Valid local ZIP restores through the public boundary

- **WHEN** `LocalArchiveRestoreSource` identifies a supported Odoo ZIP and the target is absent
- **THEN** `EnvironmentResource.refresh_database_command()` restores `dump.sql` and filestore through the existing guarded stages and returns the confirmed target

#### Scenario: Source archive is preserved

- **WHEN** local-archive restore succeeds or fails
- **THEN** the caller-owned archive remains unchanged and only private staging artifacts are cleaned

#### Scenario: Result and plans are path-redacted

- **WHEN** a local-archive command is previewed, succeeds, fails, or is interrupted
- **THEN** bounded Rich, JSON, and TOON projections identify the source kind and sanitized digest evidence without exposing the source or private snapshot path

#### Scenario: PostgreSQL custom dump is explicitly unsupported

- **WHEN** `LocalArchiveRestoreSource` content begins with the PostgreSQL custom-format signature `PGDMP`
- **THEN** restore fails with code `backup_unsupported_format` and message `unsupported local backup format: PostgreSQL custom dump` before ZIP validation or any mutation and does not select the native dump restore branch

#### Scenario: Malformed ZIP remains a corrupt ZIP

- **WHEN** `LocalArchiveRestoreSource` content has a recognized ZIP-family prefix but fails existing structural or CRC validation
- **THEN** restore fails with code `backup_corrupt` and the existing sanitized corrupt-archive diagnostic before any mutation

#### Scenario: Unknown binary content fails closed

- **WHEN** `LocalArchiveRestoreSource` content is neither a recognized ZIP-family input nor a PostgreSQL custom-format dump
- **THEN** restore fails with code `backup_unknown_format` and message `unrecognized local backup format` before ZIP validation or any mutation

#### Scenario: Dry-run distinguishes local format outcomes

- **WHEN** the public command `odcli db restore --file FILE --dry-run --format json` receives respectively a valid Odoo ZIP, a PostgreSQL custom dump, a malformed ZIP, or unknown bytes
- **THEN** it plans the valid ZIP or returns the corresponding stable typed diagnostic without database mutation, subprocess execution, source modification, or path disclosure

#### Scenario: Other unsupported local sources remain rejected

- **WHEN** `LocalArchiveRestoreSource` identifies a remote URL or an archive requiring format conversion
- **THEN** restore fails before database mutation with a typed validation or configuration error and performs no conversion
