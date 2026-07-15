## ADDED Requirements

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

### Requirement: Database model

`Database` MUST быть `msgspec.Struct` с `frozen=True, forbid_unknown_fields=True, kw_only=True` и полями:
- `name: str`;
- `backup: Backup | NoBackup`.

Точное объявление:

```python
class Database(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    name: str
    backup: Backup | NoBackup
```

Конструкция: `Database(name="staging", backup=<Backup>)` или `Database(name="test", backup=NoBackup())`. `kw_only=True` требует keyword arguments.

Модель MUST NOT содержать методов с side effects.

#### Scenario: База с известным backup

- **WHEN** database "staging" имеет restores-mapping
- **THEN** `Database(name="staging", backup=<Backup>)` конструируется и `db.backup` is `Backup`

#### Scenario: База без backup

- **WHEN** database "test" не имеет restores-mapping
- **THEN** `Database(name="test", backup=NoBackup())` конструируется и `db.backup` is `NoBackup`

### Requirement: NoBackup nullable model

`NoBackup` MUST быть `msgspec.Struct` с `frozen=True, forbid_unknown_fields=True` и теми же полями, что `Backup`, но нулевыми значениями:

```python
class NoBackup(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    id: uuid.UUID = uuid.UUID(int=0)
    source_base_url: str = ""
    database_name: str = ""
    format: BackupFormat | None = None
    filestore_requested: bool = False
    path: str = ""
    filename: str = ""
    size_bytes: int = 0
    sha256: str = ""
    downloaded_at: datetime = datetime.fromtimestamp(0, UTC)
```

Все default-значения MUST быть статическими (вычисляются при определении класса, НЕ `field(default_factory=...)`). `uuid.UUID(int=0)` (NIL UUID) и `datetime.fromtimestamp(0, UTC)` — frozen значения, безопасны как статические defaults.

`models.py` MUST импортировать `UTC` из `datetime`: `from datetime import UTC, datetime`.

Все поля MUST иметь default-значения, позволяя `NoBackup()` без аргументов.

`NoBackup` намеренно БЕЗ `kw_only=True` (соответствует `Backup`, который тоже без `kw_only`), чтобы поддерживать `NoBackup()` без аргументов через defaults.

Модель MUST NOT содержать методов с side effects.

Caller MAY различать `Backup` и `NoBackup` через `db.backup.format is not None` или `db.backup.id != uuid.UUID(int=0)`.

#### Scenario: Доступ к полям NoBackup

- **WHEN** `db.backup` is `NoBackup()`
- **THEN** `db.backup.downloaded_at` returns `datetime.fromtimestamp(0, UTC)`, `db.backup.format` returns `None`, `db.backup.id` returns `uuid.UUID(int=0)`

#### Scenario: Конструкция без аргументов

- **WHEN** `NoBackup()` вызывается без аргументов
- **THEN** возвращается instance со всеми нулевыми default-значениями

### Requirement: InstanceConfig cluster-key fields

`InstanceConfig` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` (без изменений к существующим полям) и включать новые поля:
- `db_host: str | None = field(default=None)`;
- `db_port: int | None = field(default=None)`;
- `db_user: str | None = field(default=None)`;
- `db_password: str | None = field(default=None, repr=False)`.

`db_host`, `db_port`, `db_user` MAY появляться в repr. `db_password` MUST использовать `repr=False`.

`InstanceConfig.__repr__` (существующий custom `__repr__`) MUST NOT include `db_password`. Существующий `__repr__` MUST быть обновлён для показа `db_host`, `db_port`, `db_user` (не-redacted). Пример:
```python
f"InstanceConfig(base_url={self.base_url!r}, master_pwd=<redacted>, "
f"db_host={self.db_host!r}, db_port={self.db_port!r}, db_user={self.db_user!r}, "
f"configured_database_names={self.configured_database_names!r})"
```

#### Scenario: from_config() заполняет cluster-key

- **WHEN** `InstanceFactory.from_config("odoo.conf")` читает `db_host=localhost`, `db_port=5432`, `db_user=odoo`, `db_password=secret`
- **THEN** `InstanceConfig.db_host == "localhost"`, `db_port == 5432`, `db_user == "odoo"`, `db_password` не виден в repr

#### Scenario: __call__() не имеет cluster-key

- **WHEN** `client.instance("http://localhost:8069")` создаёт инстанс
- **THEN** `InstanceConfig.db_host is None`, `db_port is None`, `db_user is None`, `db_password is None`

### Requirement: Cluster-key заполняется из StartConfig

`InstanceFactory.from_config()` MUST копировать `db_host`, `db_port`, `db_user`, `db_password` из сконструированного `StartConfig` (того же, который назначается в `start_config`) в новые поля `InstanceConfig`. SDK MUST NOT ре-парсить odoo.conf для cluster-key.

Если `StartConfig.db_port is None` но `StartConfig.db_host is not None`, SDK MUST использовать default `5432` для `InstanceConfig.db_port`.

#### Scenario: db_port default 5432

- **WHEN** `from_config()` читает `db_host=localhost` без `db_port` в odoo.conf
- **THEN** `InstanceConfig.db_host == "localhost"`, `InstanceConfig.db_port == 5432`

#### Scenario: Полный cluster-key из StartConfig

- **WHEN** `from_config()` строит `StartConfig(db_host="localhost", db_port=5432, db_user="odoo", db_password="secret")`
- **THEN** `InstanceConfig` получает те же значения: `db_host="localhost"`, `db_port=5432`, `db_user="odoo", db_password="secret"`