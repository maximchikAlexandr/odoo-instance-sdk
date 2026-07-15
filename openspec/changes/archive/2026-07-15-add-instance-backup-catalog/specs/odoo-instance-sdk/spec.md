## MODIFIED Requirements

### Requirement: Публичная структура клиента

Публичный API MUST иметь следующую структуру:

```text
OdooClient
├── instance
│   ├── __call__(base_url, master_password=None)
│   └── from_config(path, base_url=None, master_password=None)
└── backups
    ├── list()
    ├── latest()
    ├── history()
    ├── validate()
    └── delete()

OdooInstance
├── base_url
├── configured_database_names
├── databases
│   ├── backup()
│   ├── restore()
│   ├── drop()
│   ├── list()
│   └── exists()
├── run()
├── start()
├── stop()
├── status()
└── wait_ready()
```

`client.instance(...)` MUST возвращать отдельный `OdooInstance`. `OdooInstance.databases` MUST быть единственным публичным входом к database manager конкретного instance. Server lifecycle и readiness методы (`run`, `start`, `stop`, `status`, `wait_ready`) MUST быть доступны напрямую на `OdooInstance`.

`client.database` и `client.server` MUST быть удалены. Compatibility alias или deprecation proxy MUST NOT добавляться.

`client.backups` MUST управлять локальной коллекцией скачанных backup files и MUST NOT выполнять HTTP operations Odoo.

Ресурсы MUST группировать явные процедуры и возвращать типизированные модели. Модели MUST NOT выполнять скрытые side effects.

Process registry (зарегистрированные процессы и subprocess handles) MUST храниться приватно на `OdooClient` и разделяться всеми instances. Публичный доступ к registry отсутствует.

#### Scenario: Явный remote instance

- **WHEN** пользователь вызывает `client.instance(base_url=..., master_password=...)`
- **THEN** он получает `OdooInstance`, database operations доступны через `instance.databases`, server lifecycle и readiness — напрямую через `instance.start()`, `instance.wait_ready()` и т.д.

#### Scenario: Старый API отсутствует

- **WHEN** пользователь обращается к `client.database` или `client.server`
- **THEN** атрибут отсутствует и SDK не перенаправляет вызов автоматически

### Requirement: Конфигурация клиента

`OdooClientConfig` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` и MUST содержать только общие параметры:

- путь или имя Odoo executable;
- необязательный default backup directory;
- HTTP timeout по умолчанию.

Base URL и master password MUST NOT храниться в `OdooClientConfig`.

`InstanceConfig` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` и MUST содержать normalized base URL, optional master password и informational tuple `configured_database_names`.

Поле master password MUST иметь `repr=False` и MUST NOT попадать в exception messages, stdout, stderr, SQLite или application logs SDK.

#### Scenario: Один client и несколько instances

- **WHEN** один `OdooClient` создаёт два instance с разными URL
- **THEN** каждый `DatabaseResource` использует только конфигурацию своего instance

#### Scenario: Read-only instance без password

- **WHEN** instance создан без master password
- **THEN** `list()` и `exists()` доступны, а privileged operations завершаются `MasterPasswordRequiredError` до HTTP request

### Requirement: REST-контракт database manager

Instance-bound `DatabaseResource` MUST использовать только стандартные endpoints Odoo 19.0:

- `POST /web/database/backup`;
- `POST /web/database/restore`;
- `POST /web/database/drop`;
- JSON-RPC `/web/database/list`.

Каждый request MUST строиться от normalized `OdooInstance.base_url`.

`backup()`, `restore()` и `drop()` MUST использовать master password instance как обычное form field `master_pwd` в POST body. SDK MUST NOT использовать HTTP Basic Auth: Odoo 19.0 database endpoints имеют `auth="none"` и не проверяют Basic header.

SDK MUST NOT выдавать прямой публичный доступ к произвольным Odoo HTTP endpoints.

#### Scenario: Изоляция instance

- **WHEN** database method вызван у `instance_a.databases`
- **THEN** request отправляется только на normalized base URL `instance_a`

### Requirement: Создание backup

`instance.databases.backup()` MUST поддерживать:

- имя базы;
- `BackupFormat.ZIP` или `BackupFormat.DUMP`;
- параметр `filestore`, default `True`;
- необязательный destination directory;
- необязательный timeout.

Метод MUST:

1. создать audit entry до HTTP request;
2. отправить `POST /web/database/backup` с полями Odoo 19.0 `master_pwd`, `name`, `backup_format`, `filestore`;
3. потоково записать response в `.part` file;
4. атомарно переименовать успешно скачанный file;
5. записать успех или ошибку в catalog;
6. вернуть `Backup`.

`Backup` MUST напрямую приниматься `instance.databases.restore()` другого локального instance без ручного открытия или переупаковки file.

#### Scenario: Backup remote instance

- **WHEN** remote instance имеет master password и база существует
- **THEN** backup скачивается локально, audit содержит success и метод возвращает `Backup`

#### Scenario: Ошибка скачивания

- **WHEN** HTTP request или запись file завершается ошибкой
- **THEN** partial file удаляется, audit сохраняет failure и вызывающий получает типизированное исключение

### Requirement: Каталог хранения backups

`instance.databases.backup()` MUST сохранять file:

1. в явно переданный destination directory;
2. иначе в `OdooClientConfig.backups_directory`;
3. иначе в `platformdirs.user_cache_path("odoo-instance-sdk") / "backups"`.

Catalog database MUST всегда храниться в `platformdirs.user_cache_path("odoo-instance-sdk") / "backups.sqlite3"`.

SDK MUST использовать безопасный basename из `Content-Disposition`. Final filename MUST начинаться с backup UUID. Имя HTTP response MUST NOT позволять выйти за destination directory.

Успешный backup MUST NOT удаляться автоматически.

#### Scenario: Custom destination

- **WHEN** caller передал destination directory
- **THEN** file сохраняется там, а absolute path регистрируется в общем SQLite catalog

#### Scenario: Default cache

- **WHEN** destination и client default отсутствуют
- **THEN** file и catalog создаются в стандартном user cache layout

### Requirement: Восстановление базы

`instance.databases.restore()` MUST принимать существующий доступный `Backup`, target database name и параметры Odoo 19.0 `copy` и `neutralize_database`.

Перед HTTP request метод MUST проверить:

- local instance guard;
- наличие master password;
- наличие соответствующей catalog row;
- state `available`;
- совпадение metadata объекта с catalog;
- существование и читаемость file;
- отсутствие target database.

Метод MUST отправлять multipart request в `POST /web/database/restore` и MUST NOT автоматически удалять существующую target database.

После ответа Odoo метод MUST подтвердить `exists(target_name) == True`. HTTP 200 или redirect сам по себе MUST NOT считаться успехом.

#### Scenario: Restore catalog backup

- **WHEN** target instance локальный, target database отсутствует и передан доступный `Backup`
- **THEN** SDK восстанавливает database и возвращает result только после подтверждения через list endpoint

#### Scenario: Forged или stale Backup

- **WHEN** metadata объекта не совпадает с catalog либо file отсутствует
- **THEN** restore не отправляет HTTP request и выбрасывает типизированную backup error

### Requirement: Удаление базы

`instance.databases.drop()` MUST отправлять `POST /web/database/drop` только после local guard и master password guard.

После ответа метод MUST подтвердить `exists(name) == False`. Redirect или HTTP 200 без postcondition MUST NOT считаться успехом.

#### Scenario: Успешное удаление локальной базы

- **WHEN** local database существует и Odoo успешно удаляет её
- **THEN** `drop()` возвращает `DropResult` после отрицательной проверки `exists()`

### Requirement: Server lifecycle в instance

`OdooInstance` MUST предоставлять методы `run()`, `start()`, `stop()`, `status()` и `wait_ready()` напрямую, без вложенного подресурса `instance.server`.

Process registry (зарегистрированные `OdooProcess` и subprocess handles) MUST храниться приватно на `OdooClient` и разделяться всеми instances. Публичный `client.server` MUST NOT существовать.

`instance.run()`, `start()`, `stop()` и `status()` MUST сохранять поведение существующего `ServerResource`: запуск Odoo executable, регистрация процесса, опрос статуса, остановка process group.

`instance.start(config: StartConfig)` MUST принимать `StartConfig` и возвращать `OdooProcess`. `StartConfig` остаётся `msgspec.Struct` с `forbid_unknown_fields=True`; поля не меняются. Метакласс `_StructMeta` удаляется.

#### Scenario: Запуск сервера через instance

- **WHEN** пользователь вызывает `instance.start(config)`
- **THEN** Odoo executable запускается, процесс регистрируется в общем registry на `OdooClient`, и возвращается `OdooProcess`

#### Scenario: Общий registry между instances

- **WHEN** два instance запускают по одному процессу через `instance_a.start(...)` и `instance_b.start(...)`
- **THEN** оба процесса зарегистрированы в одном registry на `OdooClient` и доступны через `instance_a.status(proc_a)` и `instance_b.status(proc_b)`

### Requirement: Readiness check

`instance.wait_ready(proc, *, timeout=60.0)` MUST выполнять GET `/web/health?db_server_status=true` на normalized `OdooInstance.base_url` без HTTP Basic Auth и без master password.

Odoo 19.0 endpoint `/web/health` имеет `auth="none"` и возвращает JSON `{"status": "pass"}` с HTTP 200. Метод MUST:

1. периодически опрашивать endpoint в течение `timeout`;
2. на каждый poll проверять, что процесс ещё alive;
3. вернуть `ReadinessResult` с `ok=True` при `status == "pass"`;
4. выбросить `ProcessExitedBeforeReady`, если процесс завершился до readiness;
5. выбросить `ReadinessTimeoutError`, если readiness не достигнут за `timeout`.

Метод MUST NOT использовать Basic Auth и MUST NOT читать `master_pwd` из `InstanceConfig`.

#### Scenario: Ready сервер

- **WHEN** процесс запущен и `/web/health` возвращает `{"status": "pass"}`
- **THEN** `wait_ready` возвращает `ReadinessResult(ok=True)`

#### Scenario: Процесс упал до readiness

- **WHEN** процесс завершился до того, как `/web/health` ответил `pass`
- **THEN** `wait_ready` выбрасывает `ProcessExitedBeforeReady`

#### Scenario: Readiness timeout

- **WHEN** `/web/health` не отвечает `pass` в течение `timeout`
- **THEN** `wait_ready` выбрасывает `ReadinessTimeoutError`

### Requirement: HTTP transport без Basic Auth

SDK MUST NOT использовать HTTP Basic Auth для запросов к Odoo 19.0. Database endpoints имеют `auth="none"` и не проверяют Basic header. `master_pwd` передаётся как обычное form field в POST body.

HTTP client для database operations MUST создаваться без `auth=`. Health endpoint опрашивается без auth.

SDK MUST предупреждать о передаче секрета по cleartext HTTP через `warn_if_cleartext_secret`: warning срабатывает при HTTP-запросе к нелокальному host, потому что `master_pwd` в form POST передаётся в cleartext.

#### Scenario: Database request без Basic Auth

- **WHEN** `backup()`, `restore()` или `drop()` отправляет POST
- **THEN** HTTP client не имеет `auth=` и `master_pwd` находится только в form body

#### Scenario: Cleartext warning для нелокального HTTP

- **WHEN** database operation выполняется к нелокальному host по HTTP (не HTTPS)
- **THEN** SDK warns о передаче секрета в cleartext один раз за процесс

### Requirement: Запрет destructive операций на нелокальных инстансах

`instance.databases.restore()` и `instance.databases.drop()` MUST быть запрещены для нелокального normalized base URL.

Local URL MUST определяться без DNS resolution:

- hostname ровно `localhost`; или
- literal IPv4/IPv6 address, для которого `ipaddress.ip_address(host).is_loopback` равно `True`.

Private network address, public address, любой иной DNS hostname и malformed URL MUST считаться нелокальными. Guard MUST выполняться до открытия HTTP connection. Override, force или unsafe flag MUST NOT существовать.

`backup()`, `list()` и `exists()` MUST поддерживать удалённые instances.

#### Scenario: Loopback разрешён

- **WHEN** instance URL использует `localhost`, `127.0.0.0/8` или `::1`
- **THEN** restore и drop проходят local guard

#### Scenario: Private network запрещена

- **WHEN** instance URL использует `10.0.0.0/8`, `172.16.0.0/12` или `192.168.0.0/16`
- **THEN** restore и drop завершаются `NonLocalInstanceError` до network request

### Requirement: Получение списка и проверка существования базы

`instance.databases.list()` MUST вызывать Odoo 19.0 JSON-RPC endpoint `/web/database/list` и возвращать tuple имён баз в порядке ответа Odoo.

SDK MUST NOT угадывать default database и MUST NOT предоставлять `resolve_default()`.

`instance.databases.exists(name)` MUST вызвать `list()` и вернуть точный membership result.

Если listing отключён или endpoint недоступен, методы MUST выбрасывать `DatabaseManagerUnavailableError`, а не возвращать пустой tuple.

#### Scenario: Несколько удалённых баз

- **WHEN** remote Odoo возвращает несколько database names
- **THEN** `list()` возвращает все names без выбора одного default

#### Scenario: Listing недоступен

- **WHEN** Odoo не предоставляет database list
- **THEN** SDK сообщает явную typed error

### Requirement: Модель запуска из готового backup

Поддерживаемый flow MUST начинаться с `Backup`, скачанного через `instance.databases.backup()` или найденного через `client.backups`.

SDK MUST NOT предоставлять создание пустой базы, module-selection resource, отдельный test resource или автоматическую политику повторного скачивания.

Решение использовать найденный backup или скачать новый MUST принимать вызывающий код по `Backup.downloaded_at`.

#### Scenario: Переиспользование свежего backup

- **WHEN** `client.backups.latest()` вернул существующий file
- **THEN** вызывающий код может сравнить `downloaded_at` со своим threshold и передать тот же `Backup` в restore

### Requirement: Точные типы, dataclass и msgspec

Классы с поведением, зависимостями или runtime-состоянием MUST быть реализованы как `@dataclass(slots=True, kw_only=True)`:

- `OdooClient`;
- `InstanceFactory`;
- `OdooInstance`;
- `ServerResource` (внутренний, не публичный ресурс; `OdooInstance` делегирует в него lifecycle и readiness);
- `DatabaseResource`;
- `BackupResource`;
- `BackupCatalog`;
- HTTP transport/client.

Неизменяемые конфигурации `OdooClientConfig` и `InstanceConfig` MUST дополнительно использовать `frozen=True`. Секретные поля MUST использовать `repr=False`.

`msgspec.Struct` MUST использоваться только для неизменяемых моделей данных без поведения:

- `Backup` — frozen;
- `BackupEvent` — frozen;
- `BackupValidationResult` — frozen;
- `BackupDeletionResult` — frozen;
- `StartConfig` — с `forbid_unknown_fields=True`, без изменений к полям; метакласс `_StructMeta` и helper `_matches` удаляются как последний источник `Any` и `type: ignore` в production code;
- существующие модели `CommandResult`, `OdooProcess`, `ProcessStatus`, `ReadinessResult`, `DropResult` — без изменений;
- `RestoreResult` — поле `source` меняет тип с удалённого `BackupArtifact` на `Backup`;
- внутренних DTO ответов Odoo HTTP API.

Перечисления `BackupFormat`, `BackupState`, `BackupEventType` и `BackupValidationStatus` MUST быть стандартными `StrEnum`.

`BackupArtifact` MUST быть удалён из public exports.

Production code и public annotations MUST NOT использовать `Any`. Ресурсы, SQLite repository и классы с зависимостями MUST NOT быть реализованы как `msgspec.Struct`.

#### Scenario: Dataclass показывает зависимости ресурса

- **WHEN** реализуется новый resource или container с логикой
- **THEN** его зависимости объявлены dataclass-полями, а ручной boilerplate `__init__` отсутствует

#### Scenario: msgspec ограничен моделями данных

- **WHEN** реализуются `Backup`, `BackupEvent` и результаты операций
- **THEN** они являются frozen `msgspec.Struct` и не содержат methods с side effects

#### Scenario: Static typing

- **WHEN** mypy проверяет production package и tests
- **THEN** проверка проходит без `Any`, необоснованных ignores и отсутствующих annotations

### Requirement: Ошибки SDK

Typed exception hierarchy MUST включать существующие ошибки и новые:

- `InvalidBaseUrlError`;
- `InstanceConfigurationError`;
- `MasterPasswordRequiredError`;
- `NonLocalInstanceError` (переименование существующего `RemoteInstanceError`);
- `BackupCatalogError`;
- `BackupNotFoundError`;
- `BackupNotAvailableError`;
- `BackupValidationUnavailableError`;
- `DatabaseManagerUnavailableError`;
- `BackupDownloadError`.

Существующие ошибки `CommandTimeoutError`, `ProcessNotFoundError`, `ProcessExitedBeforeReady`, `ReadinessTimeoutError`, `DatabaseError`, `ConfigError` и `OdooInstanceSdkError` (base) MUST остаться.

`RemoteInstanceError` MUST быть переименован в `NonLocalInstanceError`. Compatibility alias MUST NOT добавляться.

Exceptions MUST содержать operation name и безопасный context, но MUST NOT содержать master password, multipart body или полный config file.

Повреждённый backup MUST возвращать `BackupValidationStatus.INVALID`, а не выбрасывать отдельное validation exception. `BackupValidationUnavailableError` MUST использоваться только при недоступном `pg_restore` и `raise_if_unavailable=True`.

#### Scenario: Ошибка без утечки секрета

- **WHEN** instance operation завершается ошибкой
- **THEN** exception string и repr не содержат master password

## ADDED Requirements

### Requirement: Создание instance из явных параметров

`client.instance(base_url=..., master_password=None)` MUST нормализовать URL и вернуть новый `OdooInstance`.

Normalized URL MUST:

- использовать только HTTP/HTTPS;
- иметь lower-case scheme и hostname;
- не содержать credentials, query или fragment;
- не содержать path, кроме `/`;
- не содержать default port;
- не завершаться `/`.

Normalized URL MUST быть public `OdooInstance.base_url` и identity key backup catalog.

#### Scenario: Эквивалентные URL

- **WHEN** создаются instances из `HTTP://LOCALHOST:80/` и `http://localhost`
- **THEN** оба имеют identity `http://localhost`

#### Scenario: URL с path запрещён

- **WHEN** base URL содержит `/odoo`, query, fragment или embedded credentials
- **THEN** создание instance завершается `InvalidBaseUrlError`

### Requirement: Создание local instance из Odoo config

`client.instance.from_config(path, base_url=None, master_password=None)` MUST читать `[options]` через `configparser.RawConfigParser(interpolation=None)`.

Метод MUST читать `http_interface`, `http_port`, `admin_passwd`, `db_name`.

Явные arguments MUST иметь приоритет над config. При отсутствии config values MUST использовать Odoo 19.0 defaults:

- `http_port = 8069`;
- `admin_passwd = "admin"`;
- `db_name = ()`.

Base URL MUST автоматически строиться с scheme HTTP только если `http_interface` является literal loopback или `localhost`. Для absent, wildcard или non-loopback interface caller MUST явно передать local `base_url`.

Resulting URL MUST быть local; remote URL в `from_config()` MUST завершаться `InstanceConfigurationError`.

#### Scenario: Loopback config

- **WHEN** config содержит `http_interface = 127.0.0.1`, `http_port = 8070` и `admin_passwd`
- **THEN** instance получает URL `http://127.0.0.1:8070`, password из config и parsed `db_name`

#### Scenario: Wildcard interface

- **WHEN** config содержит `http_interface = 0.0.0.0` и base URL не передан
- **THEN** method завершается `InstanceConfigurationError`

#### Scenario: Explicit override

- **WHEN** method получает явные base URL и master password
- **THEN** они имеют приоритет над значениями config
