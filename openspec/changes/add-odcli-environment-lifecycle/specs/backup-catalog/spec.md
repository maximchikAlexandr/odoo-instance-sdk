## MODIFIED Requirements

### Requirement: Persistent backup catalog

`client.backups` MUST использовать SQLite file `platformdirs.user_data_dir("odoo-instance-sdk") / "catalog.sqlite3"` (durable user data, НЕ `user_cache_dir`).

Catalog MUST сохранять current backup row и append-only audit events. Failed downloads и deleted backups MUST оставаться в database.

Schema MUST versionироваться через `PRAGMA user_version`. Текущая schema version MUST быть `3`.

Catalog/ownership/audit являются durable user data, не cache. Backup metadata, environment ownership и append-only history живут только в durable catalog. Existing backup ZIP payloads могут оставаться в `user_cache_dir("odoo-instance-sdk")`, потому что их отсутствие reconciliation умеет фиксировать как missing.

Перед v2→v3 schema migration SDK MUST выполнить one-time path migration из legacy `Path(user_cache_dir("odoo-instance-sdk")) / "backups.sqlite3"` (см. `environment-catalog` spec — path migration requirement). После успешной миграции все opens используют только durable path; legacy DB не удаляется автоматически и `doctor` показывает его как migrated legacy artifact. Если durable и legacy DB уже существуют, durable является authoritative, а automatic merge запрещён и диагностируется.

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