## MODIFIED Requirements

### Requirement: Persistent backup catalog

`client.backups` MUST использовать SQLite file `platformdirs.user_data_dir("odoo-instance-sdk") / "catalog.sqlite3"` (durable user data, НЕ `user_cache_dir`).

Catalog MUST сохранять current backup row и append-only audit events. Failed downloads и deleted backups MUST оставаться в database.

Schema MUST versionироваться through Alembic revisions. The first Alembic revision SHALL create the complete current schema in one step. The `PRAGMA user_version` chain SHALL NOT remain as a migration ledger after the controlled transition.

Catalog/ownership/audit являются durable user data, не cache. Backup metadata, environment ownership и append-only history живут только в durable catalog. Existing backup ZIP payloads могут оставаться в `user_cache_dir("odoo-instance-sdk")`, потому что их отсутствие reconciliation умеет фиксировать как missing.

Перед one-time path migration из legacy `Path(user_cache_dir("odoo-instance-sdk")) / "backups.sqlite3"` (см. Durable catalog path) SDK MUST still copy the legacy file to the durable path when needed. После успешной миграции все opens используют только durable path; legacy DB не удаляется автоматически и `doctor` показывает его как migrated legacy artifact. Если durable и legacy DB уже существуют, durable является authoritative, а automatic merge запрещён и диагностируется.

Каждая public catalog operation MUST использовать короткую транзакцию, `foreign_keys=ON`, WAL mode и busy timeout 5000 ms.

`BackupCatalog` MAY быть переименован internally (`SdkCatalog`/equivalent); public API остаётся `client.backups` и `client.environments`. Catalog открывается internally и не экспортируется как `client.catalog`. Второй SQLite catalog запрещён.

#### Scenario: Повторный процесс

- **WHEN** первый Python process скачал backup и завершился
- **THEN** новый `OdooClient` загружает тот же backup из SQLite catalog (durable path)

#### Scenario: Неудачное скачивание остаётся в audit

- **WHEN** download завершается ошибкой
- **THEN** catalog содержит `download_started` и `download_failed`, даже если backup file не создан

#### Scenario: Catalog в user_data_dir, не cache

- **WHEN** `OdooClient` opens catalog
- **THEN** catalog path is `user_data_dir("odoo-instance-sdk")/catalog.sqlite3`, NOT `user_cache_dir("odoo-instance-sdk")/backups.sqlite3`

#### Scenario: Schema version 3

- **WHEN** catalog opened after migration
- **THEN** the schema is at the first Alembic revision and no `PRAGMA user_version` ledger is consulted

#### Scenario: Legacy DB не удаляется автоматически

- **WHEN** path migration completes (legacy → durable)
- **THEN** legacy `user_cache_dir/backups.sqlite3` остаётся; `doctor` показывает как migrated legacy artifact

#### Scenario: Durable authoritative при конфликте

- **WHEN** durable и legacy DB both exist
- **THEN** durable authoritative, automatic merge запрещён, `doctor` диагностирует

### Requirement: Schema migration to v3

Historical `PRAGMA user_version` steps through v3 SHALL NOT remain as a production migration ledger. Fresh installs SHALL apply only the first Alembic revision, which creates the complete current schema including `backups`, `backup_events`, `restores`, `database_events`, `environments`, and `environment_events`. Known alpha catalogues SHALL be backed up, verified equivalent to that revision, and stamped. The existing one-time cache-to-durable path migration SHALL still copy a legacy file when the durable catalogue is absent.

#### Scenario: Fresh install — neither DB exists

- **WHEN** catalog opens, neither durable `user_data_dir/catalog.sqlite3` nor legacy `user_cache_dir/backups.sqlite3` exist
- **THEN** the first Alembic revision creates the full current schema directly, no path migration, no error

#### Scenario: Legacy v0 migrated to durable v3

- **WHEN** catalog opens, legacy `user_cache_dir/backups.sqlite3` exists with `user_version = 0`, durable не существует
- **THEN** path migration copies DB to durable, the first Alembic revision is stamped after schema verification, and the legacy DB remains

#### Scenario: Существующая инсталляция v2

- **WHEN** durable catalog открывается as a known alpha catalogue whose schema already includes environments tables
- **THEN** existing rows are unchanged, schema equivalence is verified, and the first Alembic revision is stamped

#### Scenario: Повторное открытие v3-каталога

- **WHEN** catalog открывается already stamped at the first Alembic revision
- **THEN** schema не модифицируется, no-op

#### Scenario: Known alpha catalogue is stamped

- **WHEN** a known alpha catalogue is migrated to Alembic
- **THEN** a SQLite backup is created first, schema equivalence is verified, and the first revision is stamped

### Requirement: Environment child foreign keys survive catalogue migration

The first Alembic revision SHALL create `environment_events.environment_id` and `environment_copy_journal.environment_id` referencing the current `environments` table rather than a removed staging table such as `environments_v7`. Known alpha catalogues SHALL keep all child and parent rows, indexes, constraints, and identifiers when stamped. Sequential `PRAGMA user_version` repairs SHALL NOT remain in production. Subsequent schema changes SHALL be separate linear Alembic revisions.

#### Scenario: V7 catalogue migrates with child rows intact

- **WHEN** a known alpha catalogue that originated as v7 and contains environments, events, and copy-journal rows is stamped
- **THEN** all rows remain, both foreign keys target `environments`, and the first Alembic revision is recorded

#### Scenario: New event proves repaired reference

- **WHEN** stamping of a v7-era catalogue completes and a new event is inserted for an existing environment with foreign keys enabled
- **THEN** the insert succeeds and the new event is readable

#### Scenario: Fresh catalogue has current references

- **WHEN** a new catalogue is created via the first Alembic revision
- **THEN** both environment child tables reference `environments` without a staging-table name

### Requirement: Unified catalogue migration

Known alpha catalogues and all path-bearing rows SHALL remain under the unified `~/.odcli` root without changing backup UUIDs, event/restore/environment relationships, deterministic ownership, or valid backup/environment paths; retries after completion or interruption SHALL not duplicate records. The sequential v15-to-v16 `PRAGMA user_version` path SHALL NOT remain as a production step. Subsequent schema changes SHALL be separate linear Alembic revisions. [Source: GH#64 §7]

#### Scenario: Migrate existing catalogue

- **WHEN** an installation has a populated known alpha catalogue plus legacy backup and environment paths that already live under the unified root
- **THEN** stamping preserves UUID resolution and every event/restore/environment relation, and verifies the destination before any leftover legacy removal

#### Scenario: Alembic stamping after migration

- **WHEN** a known alpha catalogue is migrated and stamped
- **THEN** the first Alembic revision is recorded and no `PRAGMA user_version` ledger is consulted for future migrations

### Requirement: Backup source branch catalog migration

The first Alembic revision SHALL include exactly one nullable `source_git_branch TEXT` column on `backups`. Fresh schemas SHALL include the column directly. Existing rows SHALL remain unchanged with `NULL`; no backfill or branch inference SHALL run. Stamping SHALL preserve all backup events, restore mappings, database events, environments, and current-runtime records in the same catalog file. Sequential `PRAGMA user_version` column-add steps SHALL NOT remain in production.

Restore and environment tables SHALL NOT duplicate source branch. Code SHALL load provenance by following their existing `backup_id` foreign keys. No second catalog, metadata JSON column, or provenance table SHALL be added.

The catalog SHALL provide a restore-provenance lookup that follows the mapping to the backup audit row without requiring an available download state or readable file. Available-backup and file checks SHALL remain separate and SHALL be used only for freshness and restore-input decisions.

#### Scenario: Legacy catalog migrates

- **WHEN** a pre-Alembic catalog with backups and restore mappings is stamped
- **THEN** the nullable column is present, old backups load with unknown provenance, and all mappings/events remain intact

#### Scenario: Migration retry is idempotent

- **WHEN** catalog initialization runs again after the revision is stamped
- **THEN** it performs no duplicate alteration and existing provenance values remain unchanged

## ADDED Requirements

### Requirement: Alembic and SQLAlchemy Core replace the PRAGMA user_version chain

The catalogue SHALL use Alembic and SQLAlchemy Core (without ORM) as its migration ledger. One first Alembic revision SHALL create the complete current catalogue schema, constraints, and indexes in a single step. A clean install SHALL apply only that first revision and SHALL NOT run the historical v2–v16 chain. Known existing alpha catalogues SHALL be backed up via SQLite `.backup`, verified, and stamped with the first revision only after schema equivalence is confirmed.

After the controlled transition, the old `PRAGMA user_version` ledger, `_run_migrations()`, `_migrate_v*`, intermediate schema fixtures, and code serving only obsolete catalogue forms SHALL be removed from production code. Stale tables, fields, entities, and compatibility branches SHALL be removed only after production code and known catalogues no longer use them. Subsequent schema changes SHALL be separate linear Alembic revisions.

Complex data migrations and data-preservation checks SHALL remain explicit. Alembic autogenerate SHALL NOT be considered proof of migration correctness. ORM models SHALL NOT be added and repository queries SHALL NOT be translated away from `sqlite3` by this change.

#### Scenario: Clean install skips the historical chain

- **WHEN** a fresh catalogue is created
- **THEN** only the first Alembic revision runs and no v2–v16 step executes

#### Scenario: Known catalogue is migrated and stamped

- **WHEN** a known alpha catalogue is migrated
- **THEN** a SQLite backup is created first, the schema is verified equivalent to the first revision, and the revision is stamped

#### Scenario: Old migrator is removed

- **WHEN** production code is inspected after the transition
- **THEN** `PRAGMA user_version` as a migration ledger, `_run_migrations()`, and `_migrate_v*` are absent

#### Scenario: CI rejects multiple heads

- **WHEN** CI runs the Alembic gate
- **THEN** multiple Alembic heads and unverified schema-metadata divergence are rejected

### Requirement: Project-owned remote download records project_id

All project-owned download flows SHALL record the canonical `project_id` in `start_download()` before HTTP transfer. This includes download-only refresh and remote-backup plus local-restore. Generic SDK backup without project context SHALL remain `project_id = NULL` and SHALL be visible only in the global list. Ownership SHALL NOT be inferred from environment/restore joins or from URL/database/branch matching.

For an already-created row with `project_id = NULL`, an explicit safe relink to a resolved current project or a documented repair path SHALL be available. Automatic ambiguous backfill SHALL NOT be performed.

#### Scenario: Project download records project_id

- **WHEN** a project-owned remote download completes
- **THEN** the resulting backup row has a non-null canonical `project_id`

#### Scenario: Generic backup stays unowned

- **WHEN** `client.instance(remote_url).databases.backup(...)` is called without project context
- **THEN** the resulting backup row has `project_id = NULL`

#### Scenario: Unowned row can be relinked safely

- **WHEN** an existing unowned backup is relinked to a resolved project
- **THEN** the UUID, file, and history remain unchanged and the row gains the canonical `project_id`