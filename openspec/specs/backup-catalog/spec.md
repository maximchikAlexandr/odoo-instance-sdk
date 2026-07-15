## ADDED Requirements

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