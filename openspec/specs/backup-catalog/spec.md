## Purpose

Persistent local SQLite catalog for backup metadata, environment ownership, and append-only audit events.

## Requirements

### Requirement: Persistent backup catalog

`client.backups` MUST использовать SQLite file `platformdirs.user_data_dir("odoo-instance-sdk") / "catalog.sqlite3"` (durable user data, НЕ `user_cache_dir`).

Catalog MUST сохранять current backup row и append-only audit events. Failed downloads и deleted backups MUST оставаться в database.

Schema MUST versionироваться через `PRAGMA user_version`. Текущая schema version MUST быть `3`.

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
- **THEN** `PRAGMA user_version` is `3`

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

#### Scenario: Валидный Odoo ZIP

- **WHEN** archive содержит читаемые `dump.sql`, `manifest.json` и корректные CRC
- **THEN** validation result имеет status `valid`

#### Scenario: Повреждённый ZIP

- **WHEN** CRC повреждён или обязательный root entry отсутствует
- **THEN** validation result имеет status `invalid` и audit содержит failure

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

Catalog/ownership/audit являются durable user data, не cache:

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

### Requirement: Schema migration to v3

`_create_schema` MUST проверять `PRAGMA user_version` и выполнять:

- `< 3` (v0, v1, v2) → если legacy DB существует и durable не существует — выполнить path migration (см. requirement ниже). Затем `CREATE TABLE IF NOT EXISTS` для ВСЕХ таблиц: `restores`, `database_events` (DDL в `database-restore-tracking` spec) и `environments`, `environment_events` (DDL ниже). `PRAGMA user_version = 3`. Все `CREATE` используют `IF NOT EXISTS` — для v2 catalog `restores`/`database_events` уже существуют (no-op), для v0/v1 они создаются.
- `3` → no-op (schema актуальна).

Все `CREATE TABLE` и `CREATE INDEX` MUST использовать `IF NOT EXISTS`. Миграция MUST быть идемпотентной и не трогать существующие данные.

Если ни durable, ни legacy DB не существуют (fresh install), SDK MUST создать durable `catalog.sqlite3` с полной schema v3 (все таблицы: `backups`, `backup_events`, `restores`, `database_events`, `environments`, `environment_events`) напрямую — без path migration, без error.

#### Scenario: Fresh install — neither DB exists

- **WHEN** catalog opens, neither durable `user_data_dir/catalog.sqlite3` nor legacy `user_cache_dir/backups.sqlite3` exist
- **THEN** durable DB created with schema v3 directly, `user_version = 3`, no path migration, no error

#### Scenario: Legacy v0 migrated to durable v3

- **WHEN** catalog opens, legacy `user_cache_dir/backups.sqlite3` exists with `user_version = 0`, durable не существует
- **THEN** path migration copies DB to durable, schema migrated to v3, `user_version = 3`, legacy DB остаётся

#### Scenario: Существующая инсталляция v2

- **WHEN** durable catalog открывается с `user_version = 2`
- **THEN** таблицы `environments` и `environment_events` создаются, `user_version` становится 3, существующие rows не изменяются

#### Scenario: Повторное открытие v3-каталога

- **WHEN** catalog открывается с `user_version = 3`
- **THEN** schema не модифицируется, no-op

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
