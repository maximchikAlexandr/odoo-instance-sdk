## Context

`BackupCatalog` (SQLite, `backups.sqlite3`, schema v0) хранит `backups` и append-only `backup_events`. Существующий `_create_schema` НИКОГДА не вызывает `PRAGMA user_version` — все production-каталоги имеют `user_version = 0`.

`DatabaseResource.restore()` выполняет HTTP, подтверждает `exists()`, возвращает `RestoreResult(new_db, source=Backup)` — и не сохраняет связь backup → target database. Пользователь не может узнать, какой backup развёрнут на конкретной базе.

Ключевое наблюдение: backup знает про **источник** (`source_base_url`, `database_name`), а restore создаёт связь с **целью** — другим инстансом и базой. Один backup может быть восстановлен на N разных целей. Связь должна быть по cluster-ключу PG, а не по instance URL: инстанс может сменить порт, а PG-кластер (host, port) и имя базы — стабильны.

`InstanceConfig` сегодня имеет `base_url`, `master_password`, `configured_database_names`, `start_config`. Cluster-ключ (`db_host`, `db_port`, `db_user`, `db_password`) доступен только в `StartConfig` инстансов из `from_config()`. Для инстансов из `__call__()` cluster-ключ неизвестен.

## Goals / Non-Goals

**Goals:**
- Явная связь database ↔ backup с доступом `database.backup.downloaded_at`.
- Append-only история восстановлений (restores) и событий баз (database_events).
- Ленивая сверка mapping'а с актуальным состоянием баз через HTTP, с fallback на `psql` когда Odoo недоступен.
- Минимальная breaking surface: только `databases.list()` тип возвращаемого значения.

**Non-Goals:**
- Tracking restore-связей для инстансов без cluster-ключа (`__call__()`-инстансы).
- Обнаружение restore, выполненных мимо SDK (pg_dump/psql напрямую).
- Автоматическое обновление mapping при внешних изменениях БД.
- Поддержка `db_host` через Unix socket как отдельного cluster-ключа (нормализуется в строку `"socket"`).

## Decisions

### D1: Cluster-ключ вместо instance URL

**Решение:** restore-mapping keyed by `(db_host, db_port, database_name)` из `InstanceConfig` целевого инстанса. Cluster-key считается доступным (mapping пишется/читается) когда `db_port is not None` (db_host может быть None → нормализуется в `"socket"` для Unix socket). `db_host` не является gate-условием: socket-подключения валидны и поддерживаются.

**Альтернатива A:** ключ `(base_url, database_name)`. Отвергнута: инстанс может сменить HTTP-порт (8069 → 8070), а PG-кластер остаётся тем же — mapping потерялся бы.

**Альтернатива B:** gate `db_host is not None AND db_port is not None` (socket исключён). Отвергнута: socket-подключения — валидный localhost-сценарий для Odoo, исключать их бессмысленно.

**Следствие:** `db_host = None` нормализуется в строку `"socket"` через `normalize_db_host`. Два инстанса на одном хосте с socket-подключением разделяют cluster-key `"socket"` — принятое ограничение.

### D2: Append-only restores + computed latest (вариант A)

**Решение:** одна append-only таблица `restores`. `database.backup` вычисляется `SELECT ... ORDER BY restored_at DESC LIMIT 1`.

**Альтернатива:** current-state table `database_backup_map` + history table (вариант B). Отвергнута: два write на restore, сложнее миграция, избыточность.

**Следствие:** чтение O(log N) по индексу `restores_cluster_idx ON restores (db_host, db_port, database_name, restored_at DESC)`.

### D3: NoBackup как nullable-object с нулевыми значениями

**Решение:** `NoBackup` — `msgspec.Struct` с `frozen=True, forbid_unknown_fields=True`, теми же полями, что `Backup`, но нулевыми значениями: `uuid.UUID(int=0)`, `""`, `0`, `False`, `datetime.fromtimestamp(0, UTC)`, `format: BackupFormat | None = None`. Все default-значения статические (вычисляются при определении класса, не `default_factory`).

**Альтернатива A:** sentinel-класс `NoBackup` с `is_known`/`is_empty`. Отвергнут: требует `isinstance` проверок, несовместим с `db.backup.downloaded_at`.

**Альтернатива B:** `Backup | None`. Отвергнут пользователем: хочет полный аналог с сохранением доступа к полям.

**Следствие:** `db.backup` имеет тип `Backup | NoBackup`. `db.backup.downloaded_at` → `datetime` (epoch = "нет backup"). `db.backup.format` → `BackupFormat | None` (единственный Optional — enum не имеет nil-члена и добавлять его отвергнуто). Caller различает через `db.backup.format is not None` или `db.backup.id != UUID(int=0)`.

### D4: Schema миграция v0 → v2 через PRAGMA user_version

**Решение:** `_create_schema` после `executescript` текущих `CREATE TABLE IF NOT EXISTS backups/backup_events` проверяет `PRAGMA user_version`:
- `0` → `CREATE TABLE IF NOT EXISTS restores (...)`, `CREATE TABLE IF NOT EXISTS database_events (...)`, `PRAGMA user_version = 2`. Все `CREATE TABLE` используют `IF NOT EXISTS` — существующие `backups`/`backup_events` не затрагиваются.
- `1` → те же creates для `restores`/`database_events`, `PRAGMA user_version = 2`. (Теоретическая ветка для будущих v1-каталогов; текущие production-каталоги имеют 0.)
- `2` → no-op (schema актуальна).

**ВАЖНО:** существующий `_create_schema` НИКОГДА не ставил `user_version`, поэтому все текущие каталоги имеют `user_version = 0`. Ветка `0` — единственная, которая выполняется для реальных инсталляций. `CREATE TABLE IF NOT EXISTS` гарантирует идемпотентность: повторное открытие v0-каталога создаст новые таблицы (если их нет) и поставит `user_version = 2`; повторное открытие v2-каталога — no-op.

**Альтернатива:** отдельный migration framework. Отвергнут: одна таблица, SQLite, `PRAGMA user_version` — минимальный паттерн.

### D5: Ленивая сверка в list()/exists() с идемпотентностью

**Решение:** `list()` после HTTP-запроса вычисляет множество уникальных `database_name` из `restores` для cluster-key (`SELECT DISTINCT database_name WHERE db_host=? AND db_port=?`), вычитает множество имён из `list()`, и для каждой разницы записывает ровно один `database_events 'dropped'`. `exists(name)` сверяет ТОЛЬКО `name` (не все tracked databases).

**Идемпотентность:** перед записью `dropped` SDK MUST проверить, что последний event для `(cluster, database_name)` не `dropped` (т.е. `SELECT event_type FROM database_events WHERE ... ORDER BY sequence DESC LIMIT 1` не равен `'dropped'`). Если последний event уже `dropped` — SDK MUST skip запись. Это предотвращает неограниченный рост при повторных `list()` вызовах.

`normalize_db_host` применяется на ВСЕХ путях чтения и записи (`record_restore`, `record_database_dropped`, `latest_restore`, reconciliation SELECTs).

**Альтернатива:** отдельный метод `reconcile()`. Отвергнута: пользователь явно просил ленивое обновление при обычных операциях.

### D6: Fallback-verify через psql

**Решение:** psql fallback применяется ТОЛЬКО к `current()` и `exists(name)` — методам, проверяющим одну конкретную базу. `list()` НЕ имеет psql fallback (psql проверяет одну базу, не умеет перечислять); `list()` всегда пропагандирует `DatabaseManagerUnavailableError`.

`current()`/`exists()` при `DatabaseManagerUnavailableError` и cluster-ключе (`db_port is not None` AND `db_user is not None`) выполняет:

```
psql -p <db_port> -U <db_user> -d postgres -t -A -c "SELECT 1 FROM pg_database WHERE datname='<escaped_name>'"
```

`-h` передаётся ТОЛЬКО когда `db_host is not None` (для socket — опускается, psql использует default Unix socket).

- `PGPASSWORD` передаётся через env dict subprocess'у, НЕ через command line. Если `db_password is None` — `PGPASSWORD` опускается (psql использует `.pgpass` или other default auth).
- `shell` MUST быть `False` (аргументы передаются списком, без shell-интерполяции).
- `database_name` экранируется: одинарные кавычки удваиваются (`database_name.replace("'", "''")`).
- `db_user` передаётся как один argv элемент (`-U`, `<db_user>`); SDK не валидирует содержимое `db_user`.
- timeout 30s через `subprocess.run(..., timeout=30)`; `subprocess.TimeoutExpired` MUST быть пойман и обработан как inconclusive.
- `psql` обнаруживается через `shutil.which("psql")`; если None → `NoBackup`, без ошибки, без `dropped` event.

Детекция результата:
- exit 0, `completed_process.stdout.strip()` non-empty → база существует.
- exit 0, stdout пустой (после `.strip()`) → база отсутствует → `dropped` event (с идемпотентностью).
- exit non-zero (connection/auth/server error) → inconclusive → `NoBackup`, БЕЗ `dropped` event.
- `subprocess.TimeoutExpired` → inconclusive → `NoBackup`, БЕЗ `dropped` event.

**Альтернатива:** typed error `PsqlNotFoundError`. Отвергнут: fallback должен быть graceful; `psql` — best-effort.

### D7: restore-tracking ограничен from_config()-инстансами

**Решение:** `restore()` и `drop()` пишут restore-mapping только если `db_port is not None` (db_host может быть None → socket). Для `__call__()`-инстансов (db_port=None) restore работает, mapping не пишется, `database.backup` = `NoBackup`.

Mapping write в `restore()` происходит ТОЛЬКО после успешного `exists(target_name) == True` postcondition. Если postcondition fails (база не создалась), mapping НЕ пишется.

`backups` rows NEVER hard-deleted (существующий `delete()` только soft-delete через state='deleted'); поэтому FK `restores.backup_id → backups.id` всегда валиден, `ON DELETE` clause не требуется.

Reconciliation — best-effort under concurrent access: races (запись spurious `dropped` сразу после concurrent `restore`) допустимы и self-correct на следующем `restored` event. SDK MUST NOT брать global lock.

**Альтернатива:** требовать `db_host`/`db_port` параметром в `__call__()`. Отвергнута пользователем: restore без mapping приемлем.

## Risks / Trade-offs

- **[Schema v0 → v2 миграция на существующих инсталляциях]** → миграция через `PRAGMA user_version`, идемпотентная `CREATE TABLE IF NOT EXISTS` для ВСЕХ таблиц, не требует данных. Rollback: удалить `restores`/`database_events`, `PRAGMA user_version = 0`.
- **[`db_host = None` склеивает socket-инстансы]** → принято как ограничение; два инстанса с `db_host=None` на одном хосте разделяют cluster-key `'socket'`.
- **[`current()` делает network call]** → принято пользователем (медленно, но актуально); fallback на psql.
- **[База, восстановленная мимо SDK, показывает `NoBackup`]** → неизбежно; SDK не имеет внешних источников.
- **[Один backup → N баз на одном кластере]** → каждая restore-строка отдельно; `database.backup` для каждой базы указывает на один и тот же backup — корректно.
- **[Append-only restores растёт без очистки]** → принято; audit-таблицы (`backup_events`) уже append-only без очистки. Будущее: TTL/cleanup — отдельная change.
- **[psql transient failure ошибочно записывает `dropped`]** → митигировано: non-zero exit = inconclusive = `NoBackup`, без `dropped` event.

## Migration Plan

1. `_create_schema` проверяет `PRAGMA user_version`:
   - `0` → `CREATE TABLE IF NOT EXISTS restores (...)`, `CREATE TABLE IF NOT EXISTS database_events (...)`, `PRAGMA user_version = 2`. Существующие `backups`/`backup_events` уже созданы `executescript` выше — `IF NOT EXISTS` гарантирует no-op для них.
   - `1` → те же creates для `restores`/`database_events`, `PRAGMA user_version = 2`.
   - `2` → no-op.
2. Существующие backup-строки не трогаются.
3. `list()` возвращает `Database` вместо `str` — breaking change для callers. Миграция кода callers: `db_names = [d.name for d in client.instance(...).databases.list()]`.

## Open Questions

Нет. Все ключевые решения зафиксированы в explore-режиме и отражены выше.