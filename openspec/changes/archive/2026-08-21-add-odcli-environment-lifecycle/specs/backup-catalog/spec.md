## MODIFIED Requirements

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

## ADDED Requirements

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