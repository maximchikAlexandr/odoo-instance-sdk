### Requirement: Append-only restores table

Catalog MUST хранить restore-mapping в таблице `restores` (append-only). Каждая строка представляет одно событие восстановления backup в database на PG-кластере.

Точная DDL, которую `_create_schema` MUST выполнять через `CREATE TABLE IF NOT EXISTS`:

```sql
CREATE TABLE IF NOT EXISTS restores (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    db_host TEXT NOT NULL,
    db_port INTEGER NOT NULL,
    database_name TEXT NOT NULL,
    backup_id TEXT NOT NULL REFERENCES backups(id),
    restored_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS restores_cluster_idx ON restores (db_host, db_port, database_name, restored_at DESC);
```

`restored_at` MUST сохраняться как ISO-8601 TEXT через `datetime('now')` в момент insert (как существующий `occurred_at` в `backup_events`).

`db_host = None` (Unix socket) MUST нормализоваться в строку `"socket"` перед записью через helper `normalize_db_host(value: str | None) -> str`. Тот же helper MUST применяться на ВСЕХ путях чтения и записи (`record_restore`, `record_database_dropped`, `latest_restore`, reconciliation SELECTs).

`backup_id` параметр MUST быть `str(backup.id)` (строковая форма UUID), как `backups.id` хранится как TEXT.

`backups` rows MUST NEVER hard-deleted (существующий `delete()` только soft-delete через `state='deleted'`); поэтому FK `restores.backup_id → backups.id` всегда валиден, `ON DELETE` clause не требуется.

#### Scenario: Повторное восстановление в ту же базу

- **WHEN** backup восстановлен в database "staging" на кластере (host, port), затем drop+restore другого backup в ту же "staging"
- **THEN** restores содержит 2 строки; latest = MAX(restored_at)

#### Scenario: Socket-подключение

- **WHEN** `InstanceConfig.db_host` is None, `db_port` is 5432
- **THEN** restores-строка содержит `db_host = "socket"` (после normalize_db_host)

### Requirement: Append-only database events table

Catalog MUST хранить события жизненного цикла баз в таблице `database_events` (append-only). События привязаны к cluster-key, не к backup.

Точная DDL:

```sql
CREATE TABLE IF NOT EXISTS database_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    db_host TEXT NOT NULL,
    db_port INTEGER NOT NULL,
    database_name TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('restored', 'dropped')),
    occurred_at TEXT NOT NULL,
    backup_id TEXT,
    CHECK (event_type = 'dropped' OR backup_id IS NOT NULL),
    FOREIGN KEY (backup_id) REFERENCES backups(id)
);
CREATE INDEX IF NOT EXISTS database_events_cluster_idx ON database_events (db_host, db_port, database_name, sequence DESC);
```

`occurred_at` MUST сохраняться как ISO-8601 TEXT через `datetime('now')`.

`CHECK (event_type = 'dropped' OR backup_id IS NOT NULL)` гарантирует, что `restored` event всегда имеет `backup_id`.

#### Scenario: Restore пишет два события

- **WHEN** `restore()` успешно выполняется для from_config()-инстанса с cluster-ключом
- **THEN** catalog содержит new restores row и new database_events row с `event_type='restored'`, `backup_id` заполнен

#### Scenario: Drop пишет событие

- **WHEN** `drop()` успешно выполняется для from_config()-инстанса с cluster-ключом
- **THEN** catalog содержит new database_events row с `event_type='dropped'`, `backup_id` is NULL

### Requirement: Schema v0 → v2 миграция

`_create_schema` MUST после существующего `executescript` (создающего `backups` и `backup_events` через `CREATE TABLE IF NOT EXISTS`) проверять `PRAGMA user_version`:

- `0` → `CREATE TABLE IF NOT EXISTS restores (...)`, `CREATE INDEX IF NOT EXISTS restores_cluster_idx ...`, `CREATE TABLE IF NOT EXISTS database_events (...)`, `CREATE INDEX IF NOT EXISTS database_events_cluster_idx ...`, `PRAGMA user_version = 2`.
- `1` → те же creates для `restores`/`database_events`, `PRAGMA user_version = 2`. (Теоретическая ветка; текущие production-каталоги имеют 0, т.к. существующий `_create_schema` никогда не ставил `user_version`.)
- `2` → no-op (schema актуальна).

Все `CREATE TABLE` и `CREATE INDEX` MUST использовать `IF NOT EXISTS`. Миграция MUST быть идемпотентной и не трогать существующие данные в `backups` и `backup_events`.

#### Scenario: Существующая инсталляция v0

- **WHEN** catalog открывается с `user_version = 0` (все текущие production-каталоги)
- **THEN** таблицы `restores` и `database_events` создаются через `IF NOT EXISTS`, `user_version` становится 2, существующие backup rows не изменяются

#### Scenario: Повторное открытие v2-каталога

- **WHEN** catalog открывается с `user_version = 2`
- **THEN** schema не модифицируется, no-op

### Requirement: Catalog methods для restore-tracking

Catalog MUST предоставлять следующие методы (все декорированы `@_translate_sqlite_error`, как существующие методы):

```python
def record_restore(
    self,
    db_host: str | None,
    db_port: int,
    database_name: str,
    backup_id: str,
) -> None
```
MUST normalизовать `db_host` через `normalize_db_host`, вставить строку в `restores` (`restored_at = datetime('now')`) и вставить строку в `database_events` (`event_type='restored'`, `backup_id`, `occurred_at=datetime('now')`), затем единственный `commit()` (как существующие catalog operations; sqlite3 default isolation обеспечивает атомарность — НЕ явный `BEGIN`/`COMMIT`).

`record_restore` MUST всегда вставлять новую строку (NO идемпотентность, NO дедупликация); повторный restore того же backup в ту же базу создаёт отдельную restores-строку.

```python
def record_database_dropped(
    self,
    db_host: str | None,
    db_port: int,
    database_name: str,
) -> None
```
MUST normalизовать `db_host`, вставить строку в `database_events` (`event_type='dropped'`, `backup_id=NULL`, `occurred_at=datetime('now')`), затем `commit()`. Идемпотентность: перед вставкой MUST проверить последний event для `(db_host, db_port, database_name)` через `SELECT event_type FROM database_events WHERE db_host=? AND db_port=? AND database_name=? ORDER BY sequence DESC LIMIT 1`. Если последний event равен `'dropped'` → no-op (skip insert). Иначе (включая отсутствие events) → INSERT `'dropped'`. SELECT + INSERT выполняются в одной транзакции (default isolation + один `commit()`).

```python
def latest_restore(
    self,
    db_host: str | None,
    db_port: int,
    database_name: str,
) -> Backup | None
```
MUST normalизовать `db_host`, выбрать latest restores row (`ORDER BY restored_at DESC LIMIT 1`), INNER JOIN с `backups` таблицей (`backups.id = restores.backup_id`). Если join возвращает zero rows (dangling FK — не должно случаться при `foreign_keys=ON`, но обрабатывается) OR backup `state='deleted'` OR `backup.path == ''` OR `not Path(backup.path).is_file()` → метод MUST вернуть `None`. Метод MUST NOT fallback на более ранние restores rows — только latest. Семантика переиспользует `_row_to_backup` (существующий helper).

```python
def distinct_restored_database_names(
    self,
    db_host: str | None,
    db_port: int,
) -> tuple[str, ...]
```
MUST normalизовать `db_host`, вернуть `SELECT DISTINCT database_name FROM restores WHERE db_host=? AND db_port=?`. Используется reconciliation в `list()`.

Все методы MUST использовать короткую транзакцию (как существующие catalog operations).

#### Scenario: latest_restore возвращает Backup

- **WHEN** restores содержит 2 строки для (cluster, "staging"), последняя указывает на available backup с существующим файлом
- **THEN** `latest_restore` возвращает этот `Backup`

#### Scenario: latest_restore — backup файл удалён

- **WHEN** latest restores row (по restored_at DESC LIMIT 1) указывает на backup со state `deleted`
- **THEN** `latest_restore` возвращает `None`, без fallback на более ранние rows

#### Scenario: record_restore атомарность

- **WHEN** `record_restore` вызывается
- **THEN** restores row и database_events row вставляются в одной транзакции

### Requirement: Restore-mapping только для инстансов с cluster-ключом

`restore()` и `drop()` MUST писать restore-mapping (restores row, database_events row) только если `InstanceConfig.db_port is not None`.

Для инстансов из `__call__()` (без cluster-ключа) операции MUST выполняться (HTTP, exists-verify), но MUST NOT писать в `restores` или `database_events`.

`database.backup` для баз на таких инстансах MUST возвращать `NoBackup`.

#### Scenario: from_config() инстанс с cluster-ключом

- **WHEN** `restore()` вызван на инстансе с `db_host="localhost"`, `db_port=5432`
- **THEN** catalog содержит restores row и database_events "restored" row

#### Scenario: __call__() инстанс без cluster-ключа

- **WHEN** `restore()` вызван на инстансе с `db_port=None` (typical для `__call__()`)
- **THEN** HTTP restore выполняется, restores и database_events не пишутся

#### Scenario: from_config() инстанс с db_host=None

- **WHEN** `restore()` вызван на инстансе с `db_host=None` (socket), `db_port=5432`
- **THEN** cluster-ключ считается доступным, mapping пишется с `db_host="socket"`

#### Scenario: from_config() инстанс с db_port=None

- **WHEN** `restore()` вызван на инстансе с `db_host="localhost"`, `db_port=None`
- **THEN** cluster-ключ считается НЕдоступным, mapping НЕ пишется (NOT NULL constraint на db_port)

### Requirement: Ленивая сверка mapping с актуальным состоянием баз

`list()` MUST после получения результата HTTP-запроса сверить его с `restores`-таблицей (только для инстансов с cluster-ключом):

1. Вычислить множество уникальных `database_name` из `restores` для cluster-key через `catalog.distinct_restored_database_names(db_host, db_port)`.
2. Вычислить разницу: `restored_names - list_result_names`.
3. Для каждого имени из разницы вызвать `catalog.record_database_dropped(db_host, db_port, name)` (с идемпотентностью — см. следующее требование).

Сверка MUST выполняться только для инстансов с cluster-ключом (`db_port is not None`).

Сверка MUST NOT выполняться, если HTTP-запрос не вернул результат (fallback на psql — отдельное требование).

`exists(name)` сверка MUST проверять ТОЛЬКО `name` (не все tracked databases). Если `name` отсутствует в `list()` И есть restores row для (cluster, `name`) → вызвать `record_database_dropped` (с идемпотентностью).

#### Scenario: База дропнута мимо SDK

- **WHEN** restores содержит (cluster, "staging", backup_id, T1), а `list()` не возвращает "staging"
- **THEN** catalog получает один `database_events "dropped"` для (cluster, "staging")

#### Scenario: Сверка только для cluster-ключа

- **WHEN** `list()` вызван на __call__()-инстансе без cluster-ключа
- **THEN** restores и database_events не затрагиваются

#### Scenario: exists сверяет только запрошенное имя

- **WHEN** `exists("staging")` возвращает False, restores содержит "staging" и "test" для cluster-key
- **THEN** catalog получает `dropped` ТОЛЬКО для "staging", не для "test"

#### Scenario: Пустой list() с tracked restores

- **WHEN** `list()` возвращает `()` для from_config()-инстанса, restores содержит "staging" и "test"
- **THEN** catalog получает `dropped` для "staging" и "test" (оба отсутствуют в пустом списке)

### Requirement: Идемпотентность сверки

Перед записью `dropped` event для `(db_host, db_port, database_name)`, SDK MUST проверить последний event для этого cluster-key + database_name:

```sql
SELECT event_type FROM database_events
WHERE db_host=? AND db_port=? AND database_name=?
ORDER BY sequence DESC LIMIT 1
```

Если последний event уже `'dropped'` → SDK MUST skip запись. Если последний event `'restored'` или событий нет → SDK MUST записать `dropped`.

Это предотвращает неограниченный рост `database_events` при повторных `list()` вызовах.

`record_database_dropped` MUST выполнять эту проверку внутри себя (SELECT + INSERT в одной транзакции, один `commit()`).

Reconciliation — best-effort under concurrent access: races (запись spurious `dropped` сразу после concurrent `restore`) допустимы и self-correct на следующем `restored` event. SDK MUST NOT брать global lock.

#### Scenario: Повторный list() не дублирует dropped

- **WHEN** `list()` вызван дважды, "staging" отсутствует оба раза, первый вызов уже записал `dropped`
- **THEN** второй вызов НЕ записывает новый `dropped` event

#### Scenario: Restore после dropped сбрасывает идемпотентность

- **WHEN** "staging" была dropped (записан `dropped` event), затем restore в "staging" снова (записан `restored`), затем `list()` не возвращает "staging"
- **THEN** SDK записывает новый `dropped` event (последний event был `restored`, не `dropped`)

#### Scenario: Drop на базе без restores-row

- **WHEN** `drop()` вызван на базе "manual_db" (созданной вне SDK), нет prior `restored` event для неё, инстанс имеет cluster-ключ
- **THEN** `record_database_dropped` вставляет `dropped` event (последний event отсутствует → insert, не skip)

### Requirement: Fallback-verify через psql

psql fallback применяется ТОЛЬКО к `current()` и `exists(name)` — методам, проверяющим одну конкретную базу. `list()` НЕ имеет psql fallback (psql проверяет одну базу, не умеет перечислять); `list()` всегда пропагандирует `DatabaseManagerUnavailableError`.

Если `current()` или `exists(name)` получает `DatabaseManagerUnavailableError` от `list()` и инстанс имеет cluster-ключ (`db_port is not None AND db_user is not None`), SDK MUST пытаться проверить существование базы через `psql`:

```
psql [-h <db_host>] -p <db_port> -U <db_user> -d postgres -t -A -c "SELECT 1 FROM pg_database WHERE datname='<escaped_name>'"
```

`-h` передаётся ТОЛЬКО когда `db_host is not None`. Для socket (`db_host is None`) `-h` опускается — psql использует default Unix socket.

- `PGPASSWORD` передаётся через env dict subprocess'у, НЕ через command line. Если `db_password is None` — `PGPASSWORD` опускается (psql использует `.pgpass` или other default auth).
- `shell` MUST быть `False` (аргументы передаются списком, без shell-интерполяции).
- `db_user` передаётся как один argv элемент (`-U`, `<db_user>`); SDK не валидирует содержимое `db_user`.
- `database_name` MUST экранироваться: одинарные кавычки удваиваются (`database_name.replace("'", "''")`) перед подстановкой в SQL-строку.
- timeout 30 секунд через `subprocess.run(..., timeout=30)`. `subprocess.TimeoutExpired` MUST быть пойман и обработан как inconclusive.
- `psql` обнаруживается через `shutil.which("psql")`; если None → `NoBackup`, без ошибки, без `dropped` event.

Детекция результата:
- exit code `0`, `completed_process.stdout.strip()` non-empty → база существует.
- exit code `0`, `completed_process.stdout.strip()` empty → база отсутствует → записать `dropped` event (с идемпотентностью).
- exit code non-zero (connection/auth/server error) → inconclusive → `NoBackup`, БЕЗ `dropped` event, без exception.
- `subprocess.TimeoutExpired` → inconclusive → `NoBackup`, БЕЗ `dropped` event, без exception.

Если `db_user is None` (нет учётки) → propagate `DatabaseManagerUnavailableError` (psql fallback не применяется; для `current()` — propagate, НЕ `NoBackup`).

Для `current()`: psql недоступен/inconclusive (not in PATH, non-zero exit, timeout) → `Database(name, NoBackup())` без `dropped` event.
Для `exists(name)`: psql недоступен/inconclusive (not in PATH, non-zero exit, timeout, db_user is None) → propagate `DatabaseManagerUnavailableError`.

#### Scenario: Odoo недоступен, psql подтверждает базу (current)

- **WHEN** `current()` вызван на from_config()-инстансе с cluster-ключом, Odoo лежит, `psql` exit 0, `stdout.strip()` non-empty
- **THEN** `database.backup` возвращается из restores-mapping через `latest_restore`; если `latest_restore` None → `NoBackup()`

#### Scenario: Odoo недоступен, psql подтверждает базу (exists)

- **WHEN** `exists("prod")` вызван, Odoo лежит, psql exit 0, `stdout.strip()` non-empty
- **THEN** `exists()` возвращает True; reconciliation не пишется (база существует)

#### Scenario: Odoo недоступен, psql нет в PATH

- **WHEN** `current()` вызван, Odoo лежит, `shutil.which("psql")` is None
- **THEN** SDK возвращает `Database(name=<configured_name>, backup=NoBackup())` без ошибки и без `dropped` event

#### Scenario: Odoo недоступен, psql сообщает база отсутствует

- **WHEN** `current()` вызван, Odoo лежит, psql exit 0, `stdout.strip()` empty
- **THEN** `current()` возвращает `Database(name=<configured_name>, backup=NoBackup())` UNCONDITIONALLY (без `latest_restore` — database gone, mapping moot) и записывает `dropped` event (с идемпотентностью)

#### Scenario: Odoo недоступен, psql connection error

- **WHEN** `current()` вызван, Odoo лежит, psql exit non-zero (auth/connection error)
- **THEN** `current()` возвращает `Database(name=<configured_name>, backup=NoBackup())`, БЕЗ `dropped` event

#### Scenario: psql timeout

- **WHEN** `current()` вызван, Odoo лежит, psql не отвечает 30 секунд (`subprocess.TimeoutExpired`)
- **THEN** `current()` возвращает `Database(name=<configured_name>, backup=NoBackup())`, БЕЗ `dropped` event, без exception

#### Scenario: Нет db_user

- **WHEN** `current()` вызван, Odoo лежит, `db_user is None`
- **THEN** SDK propagates `DatabaseManagerUnavailableError`; psql fallback не применяется (НЕ `NoBackup`)

#### Scenario: psql inconclusive (current)

- **WHEN** `current()` вызван, Odoo лежит, psql non-zero exit OR timeout OR not in PATH (но `db_user is not None`)
- **THEN** `current()` возвращает `Database(name=<configured_name>, backup=NoBackup())`, БЕЗ `dropped` event

#### Scenario: psql inconclusive (exists)

- **WHEN** `exists("prod")` вызван, Odoo лежит, psql non-zero exit OR timeout OR not in PATH (но `db_user is not None`)
- **THEN** `exists()` propagates `DatabaseManagerUnavailableError`

#### Scenario: db_password is None

- **WHEN** `current()` вызван, Odoo лежит, `db_password is None`, `db_user="odoo"`
- **THEN** SDK запускает psql БЕЗ `PGPASSWORD` в env; auth failure → exit non-zero → inconclusive → `NoBackup`, без `dropped` event

#### Scenario: Socket-инстанс, psql fallback

- **WHEN** `current()` вызван на socket-инстансе (`db_host=None`, `db_port=5432`, `db_user="odoo"`), Odoo лежит
- **THEN** psql запускается БЕЗ `-h` (default socket), с `-p 5432 -U odoo`; результат детектируется как обычно

### Requirement: Получение current backup для database

`catalog.latest_restore(db_host, db_port, database_name) -> Backup | None` (сигнатура выше) MUST использоваться `current()` и `list()` для заполнения `Database.backup`.

Если `latest_restore` возвращает `Backup` → `Database.backup = <Backup>`. Если `None` → `Database.backup = NoBackup()`.

#### Scenario: Свежий restore существует

- **WHEN** restores содержит 2 строки для (cluster, "staging"), последняя указывает на available backup
- **THEN** `latest_restore` возвращает этот `Backup`, `Database.backup` populated

#### Scenario: Нет restores-строк

- **WHEN** для (cluster, "test") нет restores rows
- **THEN** `latest_restore` возвращает `None`, `Database.backup = NoBackup()`