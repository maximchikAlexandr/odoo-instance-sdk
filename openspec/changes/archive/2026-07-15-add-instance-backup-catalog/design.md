# Design: каталог резервных копий и instance-bound database API

## Context

SDK уже предоставляет синхронную оболочку над Odoo 19.0 CLI и database manager HTTP API. Расширение должно:

- отделить конфигурацию SDK от конфигурации конкретного Odoo instance;
- связать database operations с конкретным instance;
- сделать скачанные backup-файлы повторно обнаруживаемыми между процессами;
- сохранить полный аудит неудачных и успешных операций;
- выполнять быструю проверку без пробного восстановления базы;
- не добавлять Docker, PostgreSQL server management, scheduler и автоматическую политику свежести.

Исходники Odoo 19.0 являются источником истины:

- database controller: https://github.com/odoo/odoo/blob/19.0/addons/web/controllers/database.py
- health endpoint (`/web/health`, `auth="none"`): https://github.com/odoo/odoo/blob/19.0/addons/web/controllers/home.py
- Odoo config parser и defaults: https://github.com/odoo/odoo/blob/19.0/odoo/tools/config.py
- backup bundle и restore behavior: https://github.com/odoo/odoo/blob/19.0/odoo/service/db.py
- `pg_restore --list`: https://www.postgresql.org/docs/current/app-pgrestore.html

## Goals / Non-Goals

### Goals

- Дать точный публичный API `client.instance(...).databases`, `client.instance(...).wait_ready()` и `client.backups`.
- Поддержать несколько Odoo instances одним `OdooClient`.
- Создавать instance из явных параметров или локального `odoo.conf`.
- Идентифицировать instance нормализованным `base_url`.
- Хранить backup files и persistent audit catalog.
- Возвращать `Backup`, который напрямую принимается `restore()`.
- Позволить пользовательскому коду найти последний доступный backup и оценить его возраст.
- Проверять ZIP и custom dump без восстановления базы.
- Запретить destructive operations на нелокальных instances до открытия HTTP connection.
- Перенести server lifecycle и readiness напрямую на `OdooInstance`; убрать `client.server`.

### Non-Goals

- Автоматически решать, достаточно ли свежий backup.
- Автоматически скачивать backup при отсутствии подходящего.
- Импортировать в каталог произвольные внешние файлы.
- Отслеживать отдельное состояние `missing` при ручном удалении файла.
- Выполнять полное пробное восстановление при validation.
- Устанавливать `pg_restore`.
- Сохранять или читать удалённый `odoo.conf`.
- Поддерживать старый `client.database` или `client.server`.
- Менять sync-only модель SDK.

## Decisions

### 1. Публичный граф ресурсов

Целевой API:

```text
OdooClient
├── instance
│   ├── __call__(base_url, master_password=None) -> OdooInstance
│   └── from_config(path, base_url=None, master_password=None) -> OdooInstance
└── backups
    ├── list(...)
    ├── latest(...)
    ├── history(...)
    ├── validate(...)
    └── delete(...)

OdooInstance
├── base_url
├── configured_database_names
├── databases
│   ├── backup(...)
│   ├── restore(...)
│   ├── drop(...)
│   ├── list()
│   └── exists()
├── run(args, *, cwd=None, env=None, timeout=None) -> CommandResult
├── start(config: StartConfig, *, cwd=None, env=None) -> OdooProcess
├── stop(proc: OdooProcess, *, timeout=10.0) -> None
├── status(proc: OdooProcess) -> ProcessStatus
└── wait_ready(proc: OdooProcess, *, timeout=60.0) -> ReadinessResult
```

`client.instance` реализуется как callable `InstanceFactory`, а не как сохранённый singleton. Каждый вызов возвращает новый `OdooInstance` с собственным нормализованным URL, приватным master password, `DatabaseResource` и server lifecycle methods.

`client.backups` является локальным ресурсом клиента и не принадлежит отдельному instance.

Server lifecycle и readiness методы (`run`, `start`, `stop`, `status`, `wait_ready`) размещены напрямую на `OdooInstance`, а не в отдельном подресурсе `instance.server`. Это упрощает внешний UX: пользователь работает с одним объектом инстанса. Внутренне `OdooInstance` делегирует в приватный `ServerResource`, который использует process registry на `OdooClient`.

Process registry (`_processes`, subprocess handles) хранится приватно на `OdooClient` и разделяется всеми instances. Публичный `client.server` отсутствует.

Отклонено: глобальный `client.database`, потому что он не выражает принадлежность HTTP operations конкретному Odoo instance.

Отклонено: `client.server` как отдельный ресурс верхнего уровня — server lifecycle логически привязан к конкретному instance URL (readiness требует `base_url`), а `run()`/`start()` семантически запускают Odoo для конкретного instance.

Отклонено: вложенный `instance.server` подресурс — добавляет лишний уровень nesting без пользы для UX; lifecycle методы естественно принадлежат самому инстансу.

### 2. Конфигурация клиента и instance

`OdooClientConfig` реализуется как `@dataclass(frozen=True, slots=True, kw_only=True)` и содержит только общие параметры:

- `executable: str`;
- `backups_directory: Path | None`;
- `http_timeout_seconds: float`.

`base_url` и master password удаляются из `OdooClientConfig`.

`InstanceConfig` реализуется как `@dataclass(frozen=True, slots=True, kw_only=True)` и содержит:

- `base_url: str`;
- `master_password: str | None = field(default=None, repr=False)`;
- `configured_database_names: tuple[str, ...] = ()`.

`InstanceFactory.__call__()` принимает:

- keyword-only `base_url: str`;
- keyword-only `master_password: str | None = None`.

Master password может отсутствовать, чтобы разрешить `databases.list()` и `exists()` на instance без credentials. `backup()`, `restore()` и `drop()` при отсутствии password выбрасывают `MasterPasswordRequiredError` до HTTP request.

### 3. Нормализация URL и идентичность instance

Нормализация выполняется один раз при создании `OdooInstance`:

1. Разрешены только схемы `http` и `https`.
2. Scheme и hostname приводятся к lower case.
3. Username, password, query и fragment запрещены.
4. Path должен быть пустым или `/`.
5. Завершающий `/` удаляется.
6. Порт `80` для HTTP и `443` для HTTPS удаляется; прочий явный порт сохраняется.
7. IPv6 literal сериализуется в квадратных скобках.
8. Результат используется как `OdooInstance.base_url` и ключ истории backup.

Примеры одной идентичности:

- `HTTP://LOCALHOST:80/` → `http://localhost`
- `https://Example.COM:443` → `https://example.com`
- `http://[::1]:8069/` → `http://[::1]:8069`

DNS resolution не выполняется.

### 4. Локальность instance

Instance считается локальным только если normalized hostname:

- равен `localhost`; или
- является literal IP, для которого `ipaddress.ip_address(host).is_loopback` равно `True`.

Private network addresses и любые DNS names, кроме `localhost`, считаются нелокальными.

`restore()` и `drop()` вызывают guard до HTTP transport. Публичного флага для отключения guard нет.

### 5. Создание instance из `odoo.conf`

`client.instance.from_config(path, *, base_url=None, master_password=None)` читает стандартный INI-файл через `configparser.RawConfigParser(interpolation=None)` и секцию `[options]`, как Odoo 19.0.

Используются поля:

- `http_interface`;
- `http_port`;
- `admin_passwd`;
- `db_name`.

Приоритет значений:

1. явный аргумент метода;
2. значение из `[options]`;
3. default Odoo 19.0, если он определён ниже.

Defaults:

- `http_port = 8069`;
- `admin_passwd = "admin"`;
- `db_name = ()`.

`base_url` автоматически строится как HTTP URL только если `http_interface` явно является loopback (`localhost`, `127.0.0.0/8` или `::1`). Если interface отсутствует, пуст, равен wildcard `0.0.0.0`/`::` или нелокален, обязательным становится явный локальный `base_url`.

`from_config()` запрещает нелокальный итоговый URL. Удалённый instance создаётся только через `client.instance(base_url=...)`.

`db_name` разбирается как comma-separated tuple и сохраняется в read-only свойстве `OdooInstance.configured_database_names`. Это поле информационное; `databases.list()` всегда обращается к Odoo API и не подменяется config value.

### 6. DatabaseResource

`DatabaseResource` создаётся только внутри `OdooInstance` и получает:

- normalized base URL;
- optional master password;
- общий HTTP transport;
- `BackupCatalog`;
- default backup directory;
- default timeout.

Методы:

```text
list() -> tuple[str, ...]
exists(name: str) -> bool
backup(name, format=zip, filestore=True, destination=None, timeout=None) -> Backup
restore(backup, name, copy=False, neutralize_database=False, timeout=None) -> RestoreResult
drop(name, timeout=None) -> DropResult
```

`list()` вызывает JSON-RPC `/web/database/list` и сохраняет порядок ответа Odoo. Отдельного `resolve_default()` нет: вызывающий код получает полный список.

`exists()` вызывает `list()` и проверяет точное строковое совпадение.

### 7. Терминология и модель Backup

Термин публичного ресурса — `backups`. Успешный локальный артефакт — `Backup`. `dump` является только значением `BackupFormat`.

`Backup` — frozen `msgspec.Struct`:

| Поле | Тип |
|---|---|
| `id` | `uuid.UUID` |
| `source_base_url` | `str` |
| `database_name` | `str` |
| `format` | `BackupFormat` |
| `filestore_requested` | `bool` |
| `path` | `str` с абсолютным путём |
| `filename` | `str` |
| `size_bytes` | `int` |
| `sha256` | `str` content digest computed during download, verified before restore/validate to detect tampering |
| `downloaded_at` | timezone-aware UTC `datetime` |

Поле `filestore_requested` отражает параметр HTTP request, а не гарантирует наличие файлов в каталоге `filestore/`.

`BackupValidationResult` — frozen `msgspec.Struct` с полями: `valid: bool`, `errors: tuple[str, ...]`, `db_name: str | None`, `db_version: str | None`. Поля `db_name` и `db_version` заполняются из manifest при ZIP validation или из pg_restore output при dump validation.

`restore()` принимает только `Backup`, проверяет catalog entry, состояние `available`, совпадение metadata, существование и читаемость файла.

### 8. Расположение файлов и catalog database

Используется `platformdirs.user_cache_path("odoo-instance-sdk", ensure_exists=True)`.

Default layout:

```text
<user-cache>/odoo-instance-sdk/
├── backups/
└── backups.sqlite3
```

- `OdooClientConfig.backups_directory` меняет только default directory для новых файлов.
- Параметр `destination` в `databases.backup()` имеет приоритет над client default.
- SQLite database всегда остаётся в стандартном application cache root.
- Catalog хранит абсолютные пути и поэтому поддерживает backup files в нескольких directories.

Final filename:

```text
<backup-id>_<safe-content-disposition-filename>
```

Если Odoo не вернул допустимое filename, используется:

```text
<backup-id>_<database-name>_<UTC timestamp>.<zip|dump>
```

Скачивание идёт в соседний файл с suffix `.part`; успешное завершение выполняет atomic `os.replace()` в final path. При ошибке `.part` удаляется.

### 9. SQLite schema

Используется stdlib `sqlite3`, без ORM. Schema version хранится в `PRAGMA user_version` и для этой версии равна `1`.

```sql
CREATE TABLE backups (
    id TEXT PRIMARY KEY,
    source_base_url TEXT NOT NULL,
    database_name TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('zip', 'dump')),
    filestore_requested INTEGER NOT NULL CHECK (filestore_requested IN (0, 1)),
    state TEXT NOT NULL CHECK (state IN ('downloading', 'available', 'failed', 'deleted')),
    path TEXT,
    filename TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    started_at TEXT NOT NULL,
    downloaded_at TEXT,
    failed_at TEXT,
    deleted_at TEXT,
    error_type TEXT,
    error_message TEXT
);

CREATE INDEX backups_lookup_idx
ON backups(source_base_url, database_name, downloaded_at DESC);

CREATE INDEX backups_state_idx
ON backups(state);

CREATE TABLE backup_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    backup_id TEXT NOT NULL REFERENCES backups(id),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'download_started',
            'download_succeeded',
            'download_failed',
            'validation_succeeded',
            'validation_failed',
            'validation_unavailable',
            'deleted'
        )
    ),
    occurred_at TEXT NOT NULL,
    path TEXT,
    validator TEXT,
    exit_code INTEGER,
    message TEXT
);

CREATE INDEX backup_events_backup_idx
ON backup_events(backup_id, sequence);
```

Connection policy:

- connection на одну public operation;
- `PRAGMA foreign_keys = ON`;
- `PRAGMA journal_mode = WAL`;
- `PRAGMA busy_timeout = 5000`;
- timestamps сохраняются как UTC ISO 8601 с suffix `Z`;
- update current row и append event выполняются одной transaction;
- тексты ошибок санитизируются, не содержат master password и обрезаются до 4096 символов.

### 10. Audit lifecycle скачивания

Перед HTTP request:

1. создаётся UUID;
2. вставляется row со state `downloading`;
3. добавляется event `download_started`;
4. transaction commit.

При успехе одной transaction:

1. state меняется на `available`;
2. записываются path, filename, size и `downloaded_at`;
3. добавляется `download_succeeded`.

При ошибке одной transaction:

1. state меняется на `failed`;
2. записываются `failed_at`, безопасные error type/message;
3. добавляется `download_failed`;
4. partial file удаляется;
5. исходное типизированное исключение повторно выбрасывается.

Failed и deleted rows не удаляются из SQLite.

### 11. BackupResource query semantics

`client.backups` предоставляет:

```text
list(source_base_url=None, database_name=None, format=None) -> tuple[Backup, ...]
latest(source_base_url, database_name, format=None) -> Backup | None
history(backup_id=None, source_base_url=None, database_name=None) -> tuple[BackupEvent, ...]
validate(backup, raise_if_unavailable=False, timeout=60.0) -> BackupValidationResult
delete(backup) -> BackupDeletionResult
```

Rules:

- `list()` возвращает только state `available`, не deleted, существующие и читаемые files.
- `list()` сортирует по `downloaded_at DESC`, затем `id`.
- `latest()` применяет те же правила и возвращает первый result.
- `latest()` не принимает max age и не запускает download.
- Отсутствующие вручную files пропускаются без изменения SQLite и без создания `missing` state.
- `history()` возвращает полный audit, включая failed validations и deleted downloads, в порядке `sequence DESC`.
- URL filter нормализуется тем же алгоритмом, что instance URL.

### 12. Удаление Backup

`delete(backup)`:

1. проверяет наличие catalog row и совпадение metadata;
2. если row уже deleted, возвращает idempotent result с `already_deleted=True`;
3. удаляет file, если он существует;
4. одной transaction ставит state `deleted`, `deleted_at` и event `deleted`;
5. возвращает `BackupDeletionResult(file_existed, already_deleted, deleted_at)`.

Ручное отсутствие файла не является ошибкой для `delete()` и отражается `file_existed=False`.

### 13. ZIP validation

Для `BackupFormat.ZIP` используется только stdlib `zipfile`.

Проверка:

1. file существует и catalog row доступен;
2. `zipfile.is_zipfile(path)` возвращает true;
3. archive содержит root entries `dump.sql` и `manifest.json`;
4. `ZipFile.testzip()` возвращает `None`, то есть CRC и decompression всех entries успешны;
5. `manifest.json` читается и декодируется как JSON object.

Полная распаковка на filesystem не выполняется. `testzip()` фактически читает и распаковывает все members для CRC.

Результат `valid` создаёт `validation_succeeded`; любое нарушение создаёт `validation_failed` и result `invalid`.

### 14. Dump validation

Для `BackupFormat.DUMP` используется:

```text
pg_restore --list <absolute-path>
```

- Binary ищется через `shutil.which("pg_restore")`.
- SDK не устанавливает PostgreSQL client.
- Exit code `0` означает `valid`.
- Non-zero exit означает `invalid`; stderr сохраняется в безопасном сообщении.
- Timeout validation означает `invalid` с отдельным reason.
- Если binary отсутствует:
  - при `raise_if_unavailable=False` возвращается status `unavailable` и event `validation_unavailable`;
  - при `raise_if_unavailable=True` event всё равно записывается, затем выбрасывается `BackupValidationUnavailableError`.

`pg_verifybackup` не используется: он проверяет physical base backups PostgreSQL cluster, а Odoo `dump` является custom-format `pg_dump` archive.

### 15. Restore и drop

`restore()` и `drop()` сохраняют существующие Odoo 19.0 endpoint contracts.

Перед restore:

- local guard;
- master password guard;
- catalog и file validation;
- проверка отсутствия target database через `exists()`.

После restore подтверждается `exists(name) == True`.

Перед drop:

- local guard;
- master password guard.

После drop подтверждается `exists(name) == False`.

HTTP 200 или redirect без postcondition check не считается успехом.

### 16. Server lifecycle и readiness в instance

Server lifecycle и readiness методы размещены напрямую на `OdooInstance`, без вложенного подресурса `instance.server`. Внутренне `OdooInstance` держит приватный `ServerResource`, которому делегирует `run`, `start`, `stop`, `status`, `wait_ready`. `base_url` для readiness берётся из `OdooInstance.base_url`.

Process registry (`dict[str, OdooProcess]` и subprocess handles) хранится приватно на `OdooClient` и разделяется всеми instances. Публичного `client.server` не существует.

`ServerResource` создаётся только внутри `OdooInstance` и получает:

- `OdooClient` (для доступа к process registry и subprocess handles);
- normalized `base_url`;
- `OdooClientConfig.executable`;
- default `http_timeout`.

Методы `OdooInstance` (делегируют в `ServerResource`):

```text
run(args, *, cwd=None, env=None, timeout=None) -> CommandResult
start(config: StartConfig, *, cwd=None, env=None) -> OdooProcess
stop(proc: OdooProcess, *, timeout=10.0) -> None
status(proc: OdooProcess) -> ProcessStatus
wait_ready(proc: OdooProcess, *, timeout=60.0) -> ReadinessResult
```

`run()`, `start()`, `stop()`, `status()` идентичны текущей реализации `ServerResource`, но вызываются через `instance.run(...)`, `instance.start(...)` и т.д., а не `client.server.run(...)`.

`wait_ready()` выполняет GET `/web/health?db_server_status=true` без Basic Auth и без master password. Odoo 19.0 endpoint `/web/health` имеет `auth="none"` и не требует credentials. `poll_health` принимает `base_url` и `alive_check` как явные аргументы и не обращается к `OdooClientConfig`.

`_health.py` упрощается:

- `poll_health(base_url: str, *, timeout, poll_interval, alive_check)` — без `config`, без Basic Auth;
- GET request без `auth=`;
- `warn_if_cleartext_secret(base_url, stacklevel)` вызывается при HTTP к нелокальному host (master password в form POST по HTTP — тоже cleartext риск, даже без Basic Auth).

`StartConfig` остаётся `msgspec.Struct` с `forbid_unknown_fields=True`, без изменений к полям. `StartConfig` — модель данных без зависимостей, поэтому msgspec корректен. Метакласс `_StructMeta` и helper `_matches` удаляются: после перевода `OdooClientConfig` в frozen dataclass ни один остающийся `msgspec.Struct` не содержит union-типов с custom классами (только `str | None`, `int | None`, `Literal | None`), которые `msgspec.convert()` умеет валидировать. Удаление `_StructMeta` убирает последний источник `Any` и `type: ignore` в production code.

Существующие публичные модели `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult` сохраняют принятую msgspec.Struct реализацию. `DropResult` также сохраняет реализацию без изменений.

`RestoreResult` меняет поле `source` с удалённого `BackupArtifact` на `Backup` — это вынужденное изменение из-за удаления `BackupArtifact`.

### 17. HTTP transport и Basic Auth

Odoo 19.0 database endpoints (`/web/database/*`) имеют `auth="none"` и не проверяют Basic Auth. `master_pwd` передаётся как обычное form field в POST body. SDK убирает Basic Auth из всех HTTP requests:

- `DatabaseResource._http()` создаёт `httpx.Client(timeout=...)` без `auth=`;
- `/web/health` — GET без auth;
- `/web/database/backup`, `/web/database/restore`, `/web/database/drop` — POST с form field `master_pwd`;
- JSON-RPC `/web/database/list` — POST без auth.

`warn_if_cleartext_auth` переименовывается в `warn_if_cleartext_secret` и срабатывает при HTTP к нелокальному host, потому что `master_pwd` в form POST по HTTP передаётся в cleartext даже без Basic Auth.

### 18. Граница между dataclass и msgspec

Классы, которые содержат зависимости, состояние выполнения или методы, реализуются через `dataclasses`:

- `OdooClient`;
- `InstanceFactory`;
- `OdooInstance`;
- `ServerResource`;
- `DatabaseResource`;
- `BackupResource`;
- `BackupCatalog`;
- HTTP transport/client;
- `OdooClientConfig`;
- `InstanceConfig`.

Для них используется `@dataclass(slots=True, kw_only=True)`. Для неизменяемой конфигурации дополнительно используется `frozen=True`. Поля с master password и другими секретами объявляются с `repr=False`.

`msgspec.Struct` используется только для типизированных данных на публичных и HTTP-границах, без поведения и зависимостей:

- `Backup` — `frozen=True`;
- `BackupEvent` — `frozen=True`;
- `BackupValidationResult` — `frozen=True`;
- `BackupDeletionResult` — `frozen=True`;
- `StartConfig` (с `forbid_unknown_fields=True`, без `_StructMeta` — метакласс удаляется);
- `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`, `DropResult` — без изменений;
- `RestoreResult` — поле `source` меняет тип на `Backup`;
- внутренние DTO ответов Odoo HTTP API, включая health, database list и JSON-RPC error.

Новые перечисления реализуются стандартным `enum.StrEnum`:

- `BackupFormat`;
- `BackupState`;
- `BackupEventType`;
- `BackupValidationStatus`.

`BackupArtifact` удаляется.

Добавляются исключения:

- `InvalidBaseUrlError`;
- `InstanceConfigurationError`;
- `MasterPasswordRequiredError`;
- `NonLocalInstanceError` (переименование `RemoteInstanceError`);
- `BackupCatalogError`;
- `BackupNotFoundError`;
- `BackupNotAvailableError`;
- `BackupValidationUnavailableError`.

Повреждённый или неподходящий backup не вызывает отдельное `BackupValidationError`: `validate()` возвращает `BackupValidationResult` со статусом `invalid`. Исключение используется только когда проверка недоступна и вызов выполнен с `raise_if_unavailable=True`.

Production annotations не используют `Any`. `msgspec.Struct` не используется для ресурсов, SQLite repository и других классов с поведением.

