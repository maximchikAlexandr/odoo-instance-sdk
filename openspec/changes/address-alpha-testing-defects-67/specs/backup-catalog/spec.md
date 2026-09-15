## MODIFIED Requirements

### Requirement: Persistent backup catalog

`client.backups` MUST использовать SQLite file `platformdirs.user_data_dir("odoo-instance-sdk") / "catalog.sqlite3"` (durable user data, НЕ `user_cache_dir`).

Catalog MUST сохранять current backup row и append-only audit events. Failed downloads и deleted backups MUST оставаться в database.

Schema MUST versionироваться through Alembic revisions. The first Alembic revision SHALL create the complete current schema in one step. The `PRAGMA user_version` chain SHALL NOT remain as a migration ledger after the controlled transition.

Catalog/ownership/audit являются durable user data, не cache. Backup metadata, environment ownership и append-only history живут только в durable catalog. Existing backup ZIP payloads могут оставаться в `user_cache_dir("odoo-instance-sdk")`, потому что их отсутствие reconciliation умеет фиксировать как missing.

Перед v2→v3 schema migration SDK MUST выполнить one-time path migration из legacy `Path(user_cache_dir("odoo-instance-sdk")) / "backups.sqlite3"` (см. ADDED requirements ниже). После успешной миграции все opens используют только durable path; legacy DB не удаляется автоматически и `doctor` показывает его как migrated legacy artifact. Если durable и legacy DB уже существуют, durable является authoritative, а automatic merge запрещён и диагностируется.

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

`_create_schema` MUST проверять `PRAGMA user_version` и выполнять:

- `< 3` (v0, v1, v2) → если legacy DB существует и durable не существует — выполнить path migration (см. requirement ниже). Затем `CREATE TABLE IF NOT EXISTS` для ВСЕХ таблиц: `restores`, `database_events` (DDL в `database-restore-tracking` spec) и `environments`, `environment_events` (DDL ниже). `PRAGMA user_version = 3`. Все `CREATE` используют `IF NOT EXISTS` — для v2 catalog `restores`/`database_events` уже существуют (no-op), для v0/v1 они создаются.
- `3` → no-op (schema актуальна).

Все `CREATE TABLE` и `CREATE INDEX` MUST использовать `IF NOT EXISTS`. Миграция MUST быть идемпотентной и не трогать существующие данные.

Если ни durable, ни legacy DB не существуют (fresh install), SDK MUST создать durable `catalog.sqlite3` с полной schema v3 (все таблицы: `backups`, `backup_events`, `restores`, `database_events`, `environments`, `environment_events`) напрямую — без path migration, без error.

After the controlled Alembic transition, this requirement's historical migration steps SHALL be removed from production code. The first Alembic revision SHALL create the full current schema for fresh installs without running the v2–v16 chain. Known alpha catalogues SHALL be backed up, verified, and stamped with the first revision.

#### Scenario: Fresh install — neither DB exists

- **WHEN** catalog opens, neither durable `user_data_dir/catalog.sqlite3` nor legacy `user_cache_dir/backups.sqlite3` exist
- **THEN** the first Alembic revision creates the full current schema directly, no path migration, no error

#### Scenario: Legacy v0 migrated to durable v3

- **WHEN** catalog opens, legacy `user_cache_dir/backups.sqlite3` exists with `user_version = 0`, durable не существует
- **THEN** path migration copies DB to durable, schema migrated to v3, `user_version = 3`, legacy DB остаётся

#### Scenario: Существующая инсталляция v2

- **WHEN** durable catalog открывается с `user_version = 2`
- **THEN** таблицы `environments` и `environment_events` создаются, `user_version` становится 3, существующие rows не изменяются

#### Scenario: Повторное открытие v3-каталога

- **WHEN** catalog открывается с `user_version = 3`
- **THEN** schema не модифицируется, no-op

#### Scenario: Known alpha catalogue is stamped

- **WHEN** a known alpha catalogue is migrated to Alembic
- **THEN** a SQLite backup is created first, schema equivalence is verified, and the first revision is stamped

### Requirement: Environment child foreign keys survive catalogue migration

The next applicable sequential catalogue migration SHALL ensure `environment_events.environment_id` and `environment_copy_journal.environment_id` reference the current `environments` table rather than a removed staging table such as `environments_v7`. It SHALL preserve all child and parent rows, indexes, constraints, and identifiers, advance `PRAGMA user_version` exactly once, remain retry-safe through the existing migration transaction/lock, and create fresh catalogues with the correct references directly. If the approved base already contains this migration, implementation SHALL retain it and add the missing behavioral regression coverage rather than create a duplicate version.

After the controlled Alembic transition, the historical migration steps SHALL be removed from production code and the first Alembic revision SHALL create fresh catalogues with correct references directly. Subsequent schema changes SHALL be separate linear Alembic revisions.

#### Scenario: V7 catalogue migrates with child rows intact

- **WHEN** a v7 catalogue containing environments, events, and copy-journal rows is opened at the current schema version
- **THEN** all rows remain, both foreign keys target `environments`, and `user_version` advances to the current sequential value

#### Scenario: New event proves repaired reference

- **WHEN** migration from v7 to current completes and a new event is inserted for an existing environment with foreign keys enabled
- **THEN** the insert succeeds and the new event is readable

#### Scenario: Fresh catalogue has current references

- **WHEN** a new catalogue is created via the first Alembic revision
- **THEN** both environment child tables reference `environments` without a staging-table name

### Requirement: Unified catalogue migration

The sequential v15-to-v16 catalogue migration and all path-bearing rows SHALL migrate under the unified root without changing backup UUIDs, event/restore/environment relationships, deterministic ownership, or valid backup/environment paths; retries after completion or interruption SHALL not duplicate records. [Source: GH#64 §7]

After the controlled Alembic transition, the `PRAGMA user_version` ledger SHALL NOT remain as a migration ledger. Subsequent schema changes SHALL be separate linear Alembic revisions.

#### Scenario: Migrate existing catalogue

- **WHEN** an installation has a populated v15 catalogue plus legacy backup and environment paths
- **THEN** migration reaches v16, preserves UUID resolution and every event/restore/environment relation, and verifies the destination before legacy removal

#### Scenario: Alembic stamping after migration

- **WHEN** a known alpha catalogue is migrated and stamped
- **THEN** the first Alembic revision is recorded and no `PRAGMA user_version` ledger is consulted for future migrations

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