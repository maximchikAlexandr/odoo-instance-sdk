## 1. Models and config

- [ ] 1.1 Импортировать `UTC` в `models.py`: `from datetime import UTC, datetime`
- [ ] 1.2 Добавить `NoBackup` (`msgspec.Struct`, `frozen=True, forbid_unknown_fields=True`) в `models.py` с нулевыми default-полями (см. models-types spec — точное объявление)
- [ ] 1.3 Добавить `Database` (`msgspec.Struct`, `frozen=True, forbid_unknown_fields=True, kw_only=True`): `name: str`, `backup: Backup | NoBackup` в `models.py`
- [ ] 1.4 Добавить поля `db_host`, `db_port`, `db_user`, `db_password` (`repr=False` только для db_password) в `InstanceConfig` (config.py)
- [ ] 1.5 Обновить `InstanceConfig.__repr__` для показа `db_host`, `db_port`, `db_user` (не-redacted); `db_password` НЕ показывать
- [ ] 1.6 В `InstanceFactory.from_config()` копировать `db_host`, `db_port`, `db_user`, `db_password` из `StartConfig` в `InstanceConfig`; если `db_host is not None AND db_port is None` → `db_port = 5432`
- [ ] 1.7 Оставить cluster-key = None для `InstanceFactory.__call__()` (default-значения полей)

## 2. Catalog schema v2

- [ ] 2.1 В `_create_schema` после существующего `executescript` добавить `CREATE TABLE IF NOT EXISTS restores (...)` + `CREATE INDEX IF NOT EXISTS restores_cluster_idx` (точная DDL — database-restore-tracking spec)
- [ ] 2.2 Добавить `CREATE TABLE IF NOT EXISTS database_events (...)` + `CREATE INDEX IF NOT EXISTS database_events_cluster_idx` (точная DDL — database-restore-tracking spec, включая `CHECK (event_type = 'dropped' OR backup_id IS NOT NULL)` и FK на backups)
- [ ] 2.3 Реализовать миграцию v0 → v2: проверить `PRAGMA user_version`, ветки 0/1/2 (см. backup-catalog spec); все `CREATE TABLE`/`CREATE INDEX` через `IF NOT EXISTS`; `PRAGMA user_version = 2` в конце
- [ ] 2.4 Тест: открытие catalog с v0 (user_version=0) создаёт restores/database_events, ставит v2, существующие backups не изменяются
- [ ] 2.5 Тест: повторное открытие v2-каталога — no-op

## 3. Catalog operations

- [ ] 3.1 `normalize_db_host(value: str | None) -> str` helper: `None → "socket"`, иначе `value`
- [ ] 3.2 `record_restore(db_host: str | None, db_port: int, database_name: str, backup_id: str) -> None` — декоратор `@_translate_sqlite_error`; нормализует db_host; INSERT restores row (`restored_at=datetime('now')`) + INSERT database_events "restored" row (атомарно, одна транзакция)
- [ ] 3.3 `record_database_dropped(db_host: str | None, db_port: int, database_name: str) -> None` — `@_translate_sqlite_error`; нормализует db_host; проверка идемпотентности (последний event != 'dropped') → INSERT database_events "dropped" (backup_id=NULL) атомарно
- [ ] 3.4 `latest_restore(db_host: str | None, db_port: int, database_name: str) -> Backup | None` — `@_translate_sqlite_error`; нормализует db_host; SELECT latest restores row (ORDER BY restored_at DESC LIMIT 1) + join backups; если state='deleted' OR `not Path(path).is_file()` → None; NO fallback на более ранние rows
- [ ] 3.5 `distinct_restored_database_names(db_host: str | None, db_port: int) -> tuple[str, ...]` — `@_translate_sqlite_error`; нормализует db_host; `SELECT DISTINCT database_name FROM restores WHERE db_host=? AND db_port=?`
- [ ] 3.6 Тесты: повторный restore, latest = MAX(restored_at), backup deleted → None, file missing → None, no fallback, idempotency dropped, restore-after-dropped resets

## 4. DatabaseResource wiring + list/exists/current

- [ ] 4.0 Wire `DatabaseResource` для доступа к cluster-key через `self._instance.config.db_host/db_port/db_user` и catalog через `self._instance._client.get_catalog()` (для reconciliation/list/exists/current)
- [ ] 4.1 `list()` возвращает `tuple[Database, ...]` вместо `tuple[str, ...]`; populate `backup` для каждого имени через `catalog.latest_restore(...)` (cluster-ключ) или `NoBackup()` (без cluster-ключа)
- [ ] 4.2 После `list()`: если cluster-ключ есть → `catalog.distinct_restored_database_names(...)` → diff с list result → `record_database_dropped` для каждого пропавшего (идемпотентно)
- [ ] 4.3 `exists(name)`: после `list()`, если name отсутствует AND cluster-ключ есть AND restores содержит row для (cluster, name) → `record_database_dropped` (идемпотентно); сверка ТОЛЬКО name
- [ ] 4.4 `__getitem__(self, index: int) -> Database` — `isinstance(index, int)` check, иначе `TypeError`; делегирует в `list()[index]`; negative indices OK; out-of-range → `IndexError`; slices → `TypeError`
- [ ] 4.5a `current()`: если `configured_database_names` is None или `()` → `Database(name="", backup=NoBackup())` БЕЗ network
- [ ] 4.5b `current()` step 3: `list()` success → name found → `latest_restore` или `NoBackup()` (с cluster-ключом) / `NoBackup()` (без); name not found → `NoBackup()` + `record_database_dropped` (с cluster-ключом, идемпотентно)
- [ ] 4.6 `current()` step 4: `DatabaseManagerUnavailableError` → cluster-ключ + db_user → psql fallback (см. section 5); без cluster-ключа OR db_user=None → propagate error
- [ ] 4.7 Тесты: list возвращает Database с backup, current для from_config/__call__, empty configured_database_names, база пропала → NoBackup + dropped, Odoo лежит + psql confirms/absent/error/timeout, Odoo лежит без cluster-ключа → propagate

## 5. Fallback-verify через psql

- [ ] 5.1 Helper `verify_database_via_psql(db_host, db_port, db_user, db_password, database_name) -> bool | None` (None = psql недоступен OR inconclusive OR db_user is None; True = exists; False = absent)
- [ ] 5.2 `shutil.which("psql")` проверка; если None → return None; если `db_user is None` → return None
- [ ] 5.3 Выполнение `psql [-h <host>] -p <port> -U <user> -d postgres -t -A -c "SELECT 1 FROM pg_database WHERE datname='<escaped>'"`; `shell=False` (args list); `-h` ТОЛЬКО если `db_host is not None` (socket — опускается); `PGPASSWORD` в env dict если `db_password is not None`, иначе опускается; `database_name.replace("'", "''")` escaping; `db_user` передаётся как один argv элемент
- [ ] 5.4 Детекция: exit 0 + `stdout.strip()` non-empty → True; exit 0 + `stdout.strip()` empty → False; exit non-zero OR `subprocess.TimeoutExpired` (timeout=30s) → None (inconclusive)
- [ ] 5.5 Интеграция в `current()` и `exists(name)`: если `list()` падает (`DatabaseManagerUnavailableError`) AND cluster-ключ (`db_port is not None`) AND `db_user is not None` → psql fallback; True → `latest_restore` or `NoBackup()` (current) / True (exists); False → `NoBackup()` UNCONDITIONALLY (без `latest_restore`) + `record_database_dropped` (идемпотентно) / False (exists); None → `NoBackup()` без dropped / propagate error (exists); без cluster-ключа OR db_user=None → propagate. `list()` НЕ имеет psql fallback (всегда propagate)
- [ ] 5.6 Тесты: psql confirms (exit 0, stdout "1"), psql absent (exit 0, stdout empty), psql error (exit non-zero), psql timeout (`TimeoutExpired`), psql not in PATH, db_user=None, db_password=None (PGPASSWORD опущен), socket-инстанс (db_host=None, без `-h`)

## 6. restore/drop write mapping

- [ ] 6.1 `restore()`: после успешного `exists(target_name) == True` postcondition, если `db_port is not None` → `catalog.record_restore(db_host, db_port, target_database_name, str(backup.id))`
- [ ] 6.2 `drop()`: после успешного `exists(name) == False` postcondition, если `db_port is not None` → `catalog.record_database_dropped(db_host, db_port, name)`
- [ ] 6.3 Тесты: restore с cluster-ключом пишет restores + database_events; restore HTTP success но postcondition fail → mapping НЕ пишется; restore без cluster-ключа не пишет; drop с cluster-ключом пишет dropped; drop без cluster-ключа не пишет

## 7. Integration and breaking-change migration

- [ ] 7.1 Обновить `databases.list()` тип возвращаемого значения в public exports и `__init__.py`
- [ ] 7.2 Обновить существующие callers `list()` в tests/examples (использовать `.name`)
- [ ] 7.3 mypy strict на production package и tests
- [ ] 7.4 ruff lint
- [ ] 7.5 Обновить README/examples: `databases.list()` возвращает `Database`, `databases[0].backup.downloaded_at`, `databases.current()`
- [ ] 7.6 CHANGELOG.md entry (BREAKING: `databases.list()` тип)