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