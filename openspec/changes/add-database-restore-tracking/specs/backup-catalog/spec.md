## MODIFIED Requirements

### Requirement: Persistent backup catalog

`client.backups` MUST использовать SQLite file `platformdirs.user_cache_path("odoo-instance-sdk") / "backups.sqlite3"`.

Catalog MUST сохранять current backup row и append-only audit events. Failed downloads и deleted backups MUST оставаться в database.

Schema MUST versionироваться через `PRAGMA user_version`. Текущая schema version MUST быть `2`.

Существующий `_create_schema` НИКОГДА не ставил `PRAGMA user_version`, поэтому все текущие production-каталоги имеют `user_version = 0`. Миграция v0 → v2 MUST выполняться через `CREATE TABLE IF NOT EXISTS` для новых таблиц `restores` и `database_events` (точная DDL в `database-restore-tracking` spec) и `PRAGMA user_version = 2`. Существующие данные в `backups` и `backup_events` MUST NOT изменяться.

Каждая public catalog operation MUST использовать короткую транзакцию, `foreign_keys=ON`, WAL mode и busy timeout 5000 ms.

#### Scenario: Повторный процесс

- **WHEN** первый Python process скачал backup и завершился
- **THEN** новый `OdooClient` загружает тот же backup из SQLite catalog

#### Scenario: Неудачное скачивание остаётся в audit

- **WHEN** download завершается ошибкой
- **THEN** catalog содержит `download_started` и `download_failed`, даже если backup file не создан

#### Scenario: Миграция v0 → v2

- **WHEN** catalog открывается с `user_version = 0` (текущие production-каталоги)
- **THEN** таблицы `restores` и `database_events` создаются через `CREATE TABLE IF NOT EXISTS`, `user_version` становится 2, существующие backup rows не изменяются

#### Scenario: Повторное открытие v2-каталога

- **WHEN** catalog открывается с `user_version = 2`
- **THEN** schema не модифицируется, no-op