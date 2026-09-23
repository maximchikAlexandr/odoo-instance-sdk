## Purpose

Persistent local SQLite catalog for backup metadata, environment ownership, and append-only audit events.

## Requirements

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

### Requirement: Audit events

Catalog MUST записывать event types:

- `download_started`;
- `download_succeeded`;
- `download_failed`;
- `validation_succeeded`;
- `validation_failed`;
- `validation_unavailable`;
- `deleted`.

Каждый event MUST содержать monotonic SQLite sequence, backup UUID, UTC timestamp и безопасный operation context.

`client.backups.history()` MUST возвращать events по `sequence DESC` и MUST поддерживать filters `backup_id`, `source_base_url`, `database_name`.

#### Scenario: Полная история lifecycle

- **WHEN** backup скачан, проверен и удалён
- **THEN** history содержит success download, validation и deletion events для одного backup UUID

### Requirement: Коллекция доступных backups

`client.backups.list()` MUST возвращать tuple `Backup` для catalog rows со state `available`, которые не deleted и имеют существующий читаемый file.

Метод MUST поддерживать optional filters:

- normalized `source_base_url`;
- exact `database_name`;
- `BackupFormat`.

Results MUST сортироваться по `downloaded_at DESC`, затем UUID.

Если file был удалён вручную, method MUST пропустить его без изменения catalog и без создания состояния `missing`.

#### Scenario: Фильтрация по instance и базе

- **WHEN** catalog содержит backups нескольких instances и databases
- **THEN** filters возвращают только точные совпадения normalized URL и database name

#### Scenario: Ручное удаление файла

- **WHEN** catalog row available, но file отсутствует
- **THEN** list не возвращает Backup и audit остаётся неизменным

### Requirement: Поиск последнего backup

`client.backups.latest(source_base_url, database_name, format=None)` MUST использовать те же eligibility rules, что `list()`, и возвращать самый новый `Backup` либо `None`.

Метод MUST NOT:

- принимать max-age policy;
- автоматически скачивать новый backup;
- изменять catalog.

Возраст MUST вычисляться вызывающим кодом по timezone-aware UTC `Backup.downloaded_at`.

#### Scenario: Последний существующий backup

- **WHEN** catalog содержит несколько доступных files одной базы
- **THEN** latest возвращает file с максимальным `downloaded_at`

#### Scenario: Подходящего backup нет

- **WHEN** доступных files для filters нет
- **THEN** latest возвращает `None` без network request

### Requirement: Удаление backup

`client.backups.delete(backup)` MUST проверить catalog identity и удалить file, если он существует.

В одной transaction method MUST установить state `deleted`, `deleted_at` и append event `deleted`.

Operation MUST быть idempotent:

- повторный вызов MUST NOT выбрасывать ошибку;
- result MUST содержать `already_deleted=True`;
- если file отсутствовал до первого вызова, result MUST содержать `file_existed=False`.

Catalog row и audit events MUST NOT удаляться.

#### Scenario: Удаление существующего файла

- **WHEN** available backup file существует
- **THEN** file удаляется, row становится deleted и audit получает event

#### Scenario: Повторное удаление

- **WHEN** delete вызывается для уже deleted backup
- **THEN** возвращается idempotent result без нового filesystem error

### Requirement: Проверка Odoo ZIP backup

`client.backups.validate()` для `BackupFormat.ZIP` MUST:

1. подтвердить catalog identity и file availability;
2. подтвердить ZIP signature;
3. подтвердить root entries `dump.sql` и `manifest.json`;
4. выполнить `ZipFile.testzip()` и требовать result `None`;
5. прочитать `manifest.json` и декодировать его как JSON object.

Метод MUST NOT распаковывать archive в temporary directory и MUST NOT восстанавливать database.

Success MUST вернуть `BackupValidationStatus.VALID` и записать `validation_succeeded`. Любая structural/CRC/JSON ошибка MUST вернуть `INVALID` и записать `validation_failed`.

Backup validation SHALL NOT reject a valid Odoo archive solely because a single ZIP member exceeds 512 MiB or the total uncompressed size exceeds 2 GiB. Those ceilings SHALL be removed as unconditional invalidity criteria. Validation SHALL remain fail-safe against path traversal, malformed ZIP, encrypted/unsupported/duplicate members, and CRC errors. Entry count SHALL stay `_MAX_ZIP_ENTRIES = 4096`. Streaming SHALL use a 65536-byte buffer; `dump.sql` and filestore SHALL NOT be read fully into memory. `_MAX_ZIP_COMPRESSION_RATIO = 100` SHALL NOT invalidate `dump.sql` by itself.

Before restore, declared uncompressed sizes SHALL be summed with overflow-safe arithmetic and compared to `shutil.disk_usage(Path(data_dir).resolve()).free` when `data_dir` is set, else `shutil.disk_usage(get_backups_dir()).free` minus reserve `max(1 GiB, 10% of that free space)`; insufficient space SHALL yield `backup_insufficient_disk` with measured, available, and reserve bytes, not a structural invalid-archive error. Optional operator maximum SHALL be `backup.max_uncompressed_bytes` in `get_config_root()/user.toml`; absent key means no ceiling; overflow SHALL yield `backup_operator_limit` with measured/allowed bytes. Corrupt ZIP/CRC SHALL be `backup_corrupt`. Path traversal/duplicate/encrypted/unsupported members, and a non-`dump.sql` member whose compression ratio exceeds 100, SHALL be `backup_unsafe`. The Odoo manifest version SHALL be `str(manifest["version"])` if present, else `f"{manifest['major_version']}.0"`. Rich/JSON/TOON SHALL distinguish those four error codes.

#### Scenario: Валидный Odoo ZIP

- **WHEN** archive содержит читаемые `dump.sql`, `manifest.json` и корректные CRC
- **THEN** validation result имеет status `valid`

#### Scenario: Повреждённый ZIP

- **WHEN** CRC повреждён или обязательный root entry отсутствует
- **THEN** validation result имеет status `invalid` и audit содержит failure

#### Scenario: large dump.sql passes validation

- **WHEN** `odcli backup validate <UUID>` runs on an archive with `dump.sql` of 1.21 GB uncompressed and total uncompressed 1.53 GB
- **THEN** structural validation passes without weakening CRC or path-safety checks

#### Scenario: very large dump modelled by metadata

- **WHEN** a test backup with a single `dump.sql` larger than 100 GB is modelled via ZIP metadata/streaming fixture
- **THEN** it is not rejected by an arbitrary byte ceiling and does not allocate comparable RAM or disk

#### Scenario: insufficient disk blocks restore with a resource error

- **WHEN** the summed uncompressed sizes exceed available disk space minus the reserve
- **THEN** restore is blocked before writes with `backup_insufficient_disk` showing measured bytes, available bytes, and the required reserve

#### Scenario: operator maximum is separate from structural validity

- **WHEN** `backup.max_uncompressed_bytes` is set and an archive exceeds it
- **THEN** `backup_operator_limit` with measured/allowed bytes is returned and structural validity is not affected

#### Scenario: ZIP bomb and traversal regressions remain blocked

- **WHEN** a ZIP bomb, path traversal, duplicate, encrypted, or unsupported-compression archive is validated
- **THEN** it is rejected as `backup_unsafe` or `backup_corrupt` and covered by tests

#### Scenario: bounded-memory streaming

- **WHEN** validation and restore process a large member
- **THEN** they use 65536-byte streaming and do not read the member wholly into memory

#### Scenario: Odoo 13 version is read correctly

- **WHEN** a standard Odoo 13 manifest with `version`, `major_version`, and `version_info` is validated
- **THEN** the result contains `13.0`, not `null`

#### Scenario: Rich/JSON/TOON distinguish error kinds

- **WHEN** a validation or restore failure is rendered
- **THEN** `backup_corrupt`, `backup_unsafe`, `backup_operator_limit`, and `backup_insufficient_disk` are distinct

### Requirement: Проверка PostgreSQL custom dump

`client.backups.validate()` для `BackupFormat.DUMP` MUST запускать:

```text
pg_restore --list <absolute-path>
```

Binary MUST находиться через `shutil.which("pg_restore")`. SDK MUST NOT устанавливать PostgreSQL client.

Rules:

- exit code `0` → `VALID`;
- non-zero exit → `INVALID`;
- timeout → `INVALID`;
- binary отсутствует и `raise_if_unavailable=False` → `UNAVAILABLE`;
- binary отсутствует и `raise_if_unavailable=True` → записать unavailable event и выбросить `BackupValidationUnavailableError`.

Default validation timeout MUST быть 60 seconds.

`pg_verifybackup` MUST NOT использоваться.

#### Scenario: pg_restore принимает archive

- **WHEN** `pg_restore --list` завершается exit code 0
- **THEN** result имеет status `valid` и audit содержит success

#### Scenario: pg_restore отсутствует без raise

- **WHEN** binary не найден и `raise_if_unavailable=False`
- **THEN** result имеет status `unavailable` и exception не выбрасывается

#### Scenario: pg_restore отсутствует с raise

- **WHEN** binary не найден и `raise_if_unavailable=True`
- **THEN** audit получает unavailable event, затем выбрасывается `BackupValidationUnavailableError`

### Requirement: Catalog identity checks

`validate()`, `delete()` и `instance.databases.restore()` MUST проверять, что:

- backup UUID существует в catalog;
- metadata объекта совпадает с catalog (включая `sha256` content digest);
- state разрешает operation.

`sha256` вычисляется во время download и сохраняется в catalog. Проверка identity сравнивает `sha256` объекта с catalog значением для обнаружения tampering.

Объект с неизвестным UUID MUST приводить к `BackupNotFoundError`. Deleted, failed или downloading row MUST приводить к `BackupNotAvailableError`.

#### Scenario: Поддельный Backup object

- **WHEN** caller передаёт Backup с неизвестным UUID или изменённым path
- **THEN** operation завершается typed error до filesystem или network side effect

### Requirement: Durable catalog path

Catalog/ownership/audit MUST be durable user data, не cache:

```text
data_root    = Path(platformdirs.user_data_dir("odoo-instance-sdk"))
catalog      = data_root / "catalog.sqlite3"
environments = data_root / "environments"
state_root   = Path(platformdirs.user_state_dir("odoo-instance-sdk"))
locks        = state_root / "locks"
```

Existing backup ZIP payloads могут оставаться в `user_cache_dir("odoo-instance-sdk")`, потому что их отсутствие reconciliation умеет фиксировать как missing. Но backup metadata, environment ownership и append-only history живут только в durable catalog.

#### Scenario: Catalog in user_data_dir

- **WHEN** `OdooClient` opens catalog
- **THEN** catalog path is `user_data_dir("odoo-instance-sdk")/catalog.sqlite3`, not `user_cache_dir`

### Requirement: One-time path migration cache → data

Перед v2→v3 schema migration SDK MUST выполнить one-time path migration из legacy `Path(user_cache_dir("odoo-instance-sdk")) / "backups.sqlite3"`:

1. Под exclusive catalog-migration lock скопировать consistent DB через SQLite backup API во временный sibling durable path.
2. fsync/atomic replace, выставить `0600`.
3. Затем мигрировать schema.

После успешной миграции все opens используют только durable path. Legacy DB MUST NOT удаляться автоматически. `doctor` MUST показывать legacy DB как migrated legacy artifact.

Если durable и legacy DB уже существуют, durable является authoritative, а automatic merge запрещён и диагностируется.

#### Scenario: Legacy DB migrated

- **WHEN** catalog opens, legacy `user_cache_dir/backups.sqlite3` exists, durable не существует
- **THEN** consistent DB copied to durable path, `0600`, schema migrated, legacy DB остаётся

#### Scenario: Both exist — durable authoritative

- **WHEN** durable и legacy DB both exist
- **THEN** durable authoritative, automatic merge запрещён, `doctor` диагностирует

#### Scenario: Legacy shown by doctor

- **WHEN** `odcli doctor` runs после migration
- **THEN** legacy DB shown как migrated legacy artifact

### Requirement: Alembic catalogue migration ledger

Catalogue schema MUST be created and upgraded exclusively through Alembic revisions recorded in `storage/catalog_migrate.py`. Opening a catalogue MUST call `ensure_catalog_migrated()` before any read or write. The historical sequential `PRAGMA user_version` chain SHALL NOT remain as a production migration ledger; it may be mentioned only as removed legacy behavior.

When a legacy cache-path SQLite file exists and the durable catalogue does not, the SDK MUST perform the one-time path migration described in the path-migration requirement, then stamp or upgrade the durable file through Alembic. Fresh installs MUST create the durable catalogue at the current Alembic head in one step without consulting `PRAGMA user_version`.

All catalogue DDL in Alembic revisions MUST use idempotent `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` semantics where applicable and MUST preserve existing rows during upgrades.

#### Scenario: Fresh install — neither DB exists

- **WHEN** catalog opens, neither durable `user_data_dir/catalog.sqlite3` nor legacy `user_cache_dir/backups.sqlite3` exist
- **THEN** durable DB is created at the current Alembic head with the complete schema, no path migration, and no `PRAGMA user_version` step

#### Scenario: Legacy cache DB migrated to durable Alembic head

- **WHEN** catalog opens, legacy `user_cache_dir/backups.sqlite3` exists, durable does not
- **THEN** path migration copies to durable, Alembic upgrades to the current head, legacy DB remains as a migrated artifact

#### Scenario: Existing durable catalogue at prior Alembic revision

- **WHEN** durable catalog opens below the current Alembic head
- **THEN** Alembic applies pending revisions once, existing rows are preserved, and no `PRAGMA user_version` ledger runs

#### Scenario: Reopen at current Alembic head

- **WHEN** catalog opens at the current Alembic head
- **THEN** schema is not modified beyond the Alembic no-op check

### Requirement: `environments` table

Catalog MUST хранить environment records в таблице `environments`:

```sql
CREATE TABLE IF NOT EXISTS environments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repository_root TEXT NOT NULL,
    git_common_dir TEXT NOT NULL,
    branch TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    generated_config_path TEXT NOT NULL,
    python_environment_path TEXT NOT NULL,
    python_environment_owned INTEGER NOT NULL,
    dependency_lock_path TEXT NOT NULL,
    http_interface TEXT NOT NULL,
    http_port INTEGER NOT NULL,
    db_mode TEXT NOT NULL,
    source_db_name TEXT,
    target_db_name TEXT,
    backup_id TEXT,
    runtime_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    removed_at TEXT,
    last_error TEXT,
    FOREIGN KEY (backup_id) REFERENCES backups(id)
);
```

Constraints (enforced в application logic или CHECK):

- одна active environment на `(git_common_dir, branch)`;
- один `http_port` на active environment;
- `copy` требует target DB и backup ID;
- `shared` запрещает owned target DB/backup semantics;
- reused Python environment имеет `python_environment_owned=false` и не является cleanup target;
- secrets и содержимое config в SQLite не сохраняются.

`id` MUST быть `str(uuid)` (строковая форма UUID). `backup_id` nullable FK → `backups.id`.

`last_error` MUST быть sanitized: не содержит passwords, config body, environment variables, или file contents; MUST быть обрезан до ≤2000 chars; newlines replaced с spaces (single-line).

`backups` rows MUST NEVER hard-deleted (существующий `delete()` только soft-delete через `state='deleted'`); поэтому FK `environments.backup_id → backups.id` всегда валиден, `ON DELETE` clause не требуется.

`environments` rows MUST NEVER hard-deleted — `remove()` ставит `state='removed'`, rows остаются для SQL-аудита.

#### Scenario: Unique active env per repo+branch

- **WHEN** checkout пытается создать second active environment для same `(git_common_dir, branch)`
- **THEN** constraint violation → `EnvironmentConflictError`

#### Scenario: Unique http_port per active environment

- **WHEN** checkout с `--http-port 8069` и 8069 уже allocated to another active environment
- **THEN** constraint violation → `EnvironmentConflictError` с port conflict details

#### Scenario: Copy requires target DB + backup

- **WHEN** `db_mode=copy` environment сохраняется без `target_db_name` or `backup_id`
- **THEN** constraint violation

### Requirement: `environment_events` table

Catalog MUST хранить append-only events в таблице `environment_events`:

```sql
CREATE TABLE IF NOT EXISTS environment_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    environment_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('checkout', 'sync', 'use', 'shell', 'remove')),
    outcome TEXT NOT NULL CHECK (outcome IN ('started', 'succeeded', 'failed')),
    occurred_at TEXT NOT NULL,
    message TEXT,
    FOREIGN KEY (environment_id) REFERENCES environments(id)
);
```

`message` MUST быть sanitized and length-limited. Exact ownership живёт в environment columns, а не в event taxonomy.

Записи environment и events после удаления MUST оставаться для SQL-аудита. Это локальный операционный аудит, не tamper-proof compliance log.

#### Scenario: Append-only events

- **WHEN** checkout succeeds
- **THEN** `environment_events` получает row `operation=checkout, outcome=succeeded`; row never deleted after remove

### Requirement: No second SQLite catalog

SDK MUST NOT добавлять ORM, отдельный сервис и второй SQLite. `EnvironmentCatalog` как отдельный файл или класс-store запрещён. Расширить существующий catalog до schema v3 и переиспользовать его WAL/concurrency/error-handling подход.

`BackupCatalog` MAY быть переименован internally (`SdkCatalog`/equivalent), если имя начнёт врать; public API остаётся `client.backups` и `client.environments`.

#### Scenario: One SQLite file

- **WHEN** SDK manages environments
- **THEN** все data в одном `catalog.sqlite3`, второго SQLite нет

### Requirement: `last_used_at` and `use` event

`last_used_at` и event `operation=use, outcome=succeeded` MUST обновляться перед SDK-managed runtime operation. Это не доказательство отсутствия ручной работы.

#### Scenario: Run updates last_used_at

- **WHEN** `odcli run` succeeds preflight
- **THEN** `last_used_at` обновляется, `environment_events` получает `use/succeeded`

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

### Requirement: Exact backup UUID resolution

The backup resource and catalogue SHALL resolve a backup only by its complete UUID for point operations. Unknown, malformed, ambiguous, row-number, filename-glob, and arbitrary-path identifiers SHALL NOT select a backup.

#### Scenario: Exact UUID exists

- **WHEN** a caller supplies the complete UUID of a catalogue row
- **THEN** the resource returns that exact backup record and its state without scanning filenames

#### Scenario: UUID is unknown

- **WHEN** a valid UUID has no catalogue row
- **THEN** the operation fails with a typed not-found result and mutates no file or audit row

### Requirement: State-aware deterministic backup queries

Backup queries SHALL expose the catalogue state and actual file presence as separate fields, SHALL support source and database filters, and SHALL sort deterministically by newest catalogue time then UUID. The default query SHALL include only available backups; an explicit all-states query SHALL also include downloading, failed, and deleted records. Large queries SHALL use a validated limit and opaque deterministic keyset cursor over a single read transaction.

#### Scenario: Missing file is not a deleted state

- **WHEN** an available catalogue row points to a file that is absent
- **THEN** the query reports `state=available` and `file_present=false` separately
- **AND** its catalogue byte count is not reported as currently occupied disk bytes

#### Scenario: All states requested

- **WHEN** a caller requests all states
- **THEN** available, downloading, failed, and deleted records appear in deterministic order without deleting history

#### Scenario: Next page is requested

- **WHEN** a caller supplies the cursor returned by a limited query
- **THEN** the next read starts strictly after the last `(time, UUID)` key and contains no duplicate from the prior page

### Requirement: Backup lifecycle exclusion

Download, restore, validation where identity must remain stable, and deletion SHALL coordinate through the existing operation-lock mechanism keyed by backup UUID plus catalogue state. A downloading backup or an available backup actively held for restore SHALL not be deleted.

#### Scenario: Delete races restore

- **WHEN** restore holds the exact backup lock and deletion is requested
- **THEN** deletion fails as busy before unlinking or recording `deleted`

#### Scenario: Delete targets downloading row

- **WHEN** the selected catalogue row is still downloading
- **THEN** deletion refuses without changing the `.part` file or audit state

### Requirement: Audit-preserving safe backup deletion by UUID

UUID deletion SHALL capture and display the exact selected file, recorded size, and known restore/environment relationships before confirmation. Under the backup lock it SHALL re-read state and identity, reject a changed target or a symlink/containment escape, unlink only the selected file, verify absence, and then append the existing deleted audit state. Restore links, UUID, and history SHALL remain. Already-deleted or already-absent files SHALL return explicit idempotent outcomes, and a filesystem failure SHALL not be recorded as deletion.

#### Scenario: Exact available backup is deleted

- **WHEN** the confirmed UUID still resolves to the captured contained regular file and it is not active
- **THEN** only that file is unlinked, absence is verified, and the catalogue records `deleted` while retaining history and restore links

#### Scenario: Path changes after preview

- **WHEN** the recorded path, file identity, containment, or symlink status differs at execution
- **THEN** deletion fails closed before unlinking and does not select a replacement by name

#### Scenario: File deletion fails

- **WHEN** the operating system refuses to remove the selected file
- **THEN** the command fails and the catalogue does not record the backup as deleted

#### Scenario: File is already absent

- **WHEN** the exact available record's file is absent and no conflicting active lifecycle exists
- **THEN** deletion returns an explicit idempotent missing-file outcome and records the auditable deleted state without claiming bytes were freed

### Requirement: Environment child foreign keys survive catalogue migration

The next applicable Alembic revision SHALL ensure `environment_events.environment_id` and `environment_copy_journal.environment_id` reference the current `environments` table rather than a removed staging table such as `environments_v7`. It SHALL preserve all child and parent rows, indexes, constraints, and identifiers, remain retry-safe through the existing migration transaction/lock, and create fresh catalogues with the correct references directly. If the approved base already contains this revision, implementation SHALL retain it and add the missing behavioral regression coverage rather than create a duplicate version.

#### Scenario: Legacy catalogue migrates with child rows intact

- **WHEN** a legacy catalogue containing environments, events, and copy-journal rows is opened and upgraded to the current Alembic head
- **THEN** all rows remain, both foreign keys target `environments`, and Alembic records the current revision exactly once

#### Scenario: New event proves repaired reference

- **WHEN** migration from v7 to current completes and a new event is inserted for an existing environment with foreign keys enabled
- **THEN** the insert succeeds and the new event is readable

#### Scenario: Fresh catalogue has current references

- **WHEN** a new catalogue is created
- **THEN** both environment child tables reference `environments` without a staging-table name

### Requirement: Direct project ownership of backups
The catalogue SHALL persist canonical `project_id` when a project command creates a backup row and SHALL scope ordinary project lists by that direct ownership rather than environment/restore linkage. `--all-projects` SHALL remain global; legacy rows SHALL remain unowned unless provenance proves exactly one deterministic owner or an explicit safe relink is performed. Zero-owner or multi-project provenance SHALL remain null, SHALL NOT appear in any project-scoped query, and SHALL remain visible globally. [Source: GH#64 §1]

#### Scenario: Downloaded backup is immediately project-visible
- **WHEN** a local or remote project download succeeds before any restore or environment link exists
- **THEN** it appears in that project's list, not another project's list, and appears globally with `--all-projects`

#### Scenario: Ambiguous legacy ownership stays unowned
- **WHEN** a legacy backup has provenance resolving to two registered projects
- **THEN** migration leaves `project_id` null, excludes the backup from both project-scoped queries, and retains it in the global query until explicit safe relink

### Requirement: Unified catalogue migration
The sequential v15-to-v16 catalogue migration and all path-bearing rows SHALL migrate under the unified root without changing backup UUIDs, event/restore/environment relationships, deterministic ownership, or valid backup/environment paths; retries after completion or interruption SHALL not duplicate records. [Source: GH#64 §7]

#### Scenario: Migrate existing catalogue
- **WHEN** an installation has a populated v15 catalogue plus legacy backup and environment paths
- **THEN** migration reaches v16, preserves UUID resolution and every event/restore/environment relation, and verifies the destination before legacy removal

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
