## ADDED Requirements

### Requirement: Backup source branch catalog migration

The existing `backups` table SHALL add exactly one nullable `source_git_branch TEXT` column through the next sequential, idempotent catalog migration. Fresh schemas SHALL include the column directly. Existing rows SHALL remain unchanged with `NULL`; no backfill or branch inference SHALL run. The migration SHALL preserve all backup events, restore mappings, database events, environments, and current-runtime records in the same catalog file.

Restore and environment tables SHALL NOT duplicate source branch. Code SHALL load provenance by following their existing `backup_id` foreign keys. No second catalog, metadata JSON column, or provenance table SHALL be added.

The catalog SHALL provide a restore-provenance lookup that follows the mapping to the backup audit row without requiring an available download state or readable file. Available-backup and file checks SHALL remain separate and SHALL be used only for freshness and restore-input decisions.

#### Scenario: Legacy catalog migrates

- **WHEN** a pre-change catalog with backups and restore mappings opens
- **THEN** one nullable column is added, old backups load with unknown provenance, and all mappings/events remain intact

#### Scenario: Migration retry is idempotent

- **WHEN** catalog initialization runs again after the migration
- **THEN** it performs no duplicate alteration and existing provenance values remain unchanged

### Requirement: Provenance in catalog queries and identity

Catalog start/success download operations, row-to-model conversion, available backup list/latest queries, and latest-restore lookup SHALL round-trip `source_git_branch`. Catalog identity verification SHALL include the nullable branch so a forged in-memory `Backup` cannot silently change provenance for an existing ID.

The branch SHALL be immutable after `start_download`; path, filename, size, checksum, state, and validation updates SHALL not rewrite it.

#### Scenario: Restore lookup retains provenance

- **WHEN** latest restore resolves a mapped backup with a known branch
- **THEN** the catalog returns a `Backup` with the same branch from the original row

#### Scenario: Missing file does not erase audit provenance

- **WHEN** a restore maps to a backup with branch `release/19` but its archive is missing or unreadable
- **THEN** provenance lookup still returns `release/19` while the separate freshness decision reports the backup unavailable

#### Scenario: Forged branch rejected

- **WHEN** a caller presents an existing backup ID with a different `source_git_branch`
- **THEN** catalog identity verification fails before restore
