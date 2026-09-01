## Purpose

Public data types, dataclasses, msgspec models, and SDK errors.
## Requirements
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
- `StartConfig` — с `forbid_unknown_fields=True` и полем `logfile: str | None`; метакласс `_StructMeta` и helper `_matches` удаляются как последний источник `Any` и `type: ignore` в production code;
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
    source_git_branch: str | None = None
```

Все default-значения MUST быть статическими (вычисляются при определении класса, НЕ `field(default_factory=...)`). `uuid.UUID(int=0)` (NIL UUID) и `datetime.fromtimestamp(0, UTC)` — frozen значения, безопасны как статические defaults.

`models.py` MUST импортировать `UTC` из `datetime`: `from datetime import UTC, datetime`.

Все поля MUST иметь default-значения, позволяя `NoBackup()` без аргументов.

`NoBackup` намеренно БЕЗ `kw_only=True` (соответствует `Backup`, который тоже без `kw_only`), чтобы поддерживать `NoBackup()` без аргументов через defaults.

Модель MUST NOT содержать методов с side effects.

Caller MAY различать `Backup` и `NoBackup` через `db.backup.format is not None` или `db.backup.id != uuid.UUID(int=0)`.

#### Scenario: Доступ к полям NoBackup

- **WHEN** `db.backup` is `NoBackup()`
- **THEN** `db.backup.downloaded_at` returns `datetime.fromtimestamp(0, UTC)`, `db.backup.format` returns `None`, `db.backup.id` returns `uuid.UUID(int=0)`, and `db.backup.source_git_branch` returns `None`

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

### Requirement: `StartConfig` preserves `logfile`

`StartConfig` MUST включать `logfile: str | None = None`. `StartConfig.from_odoo_config(path)` MUST читать option `logfile` из odoo.conf (empty → `None`).

`_build_cli_args()` MUST NOT добавлять `--logfile`. Log destination MUST оставаться только в bound config file.

#### Scenario: logfile preserved from config

- **WHEN** `StartConfig.from_odoo_config(path)` читает `logfile = /tmp/odoo.log`
- **THEN** `StartConfig.logfile == "/tmp/odoo.log"`

#### Scenario: No second log destination in argv

- **WHEN** `_build_cli_args()` builds argv for a `StartConfig` with `logfile` and `config_path` set
- **THEN** argv contains `--config` once and does not contain `--logfile`

### Requirement: `StartConfig.from_odoo_config(path)` records actual path

`StartConfig.from_odoo_config(path)` MUST устанавливать `config_path` в фактический `path` (приведённый к строке), а не ждать option `config_path` внутри файла.

Если файл содержит option `config_path`, actual `path` argument MUST иметь приоритет — значение из файла игнорируется для `config_path` field.

`_build_cli_args()` MUST передавать ровно один `--config <config_path>`. SDK MUST NOT добавлять второй временный config только из-за `db_password`, если persistent generated conf уже имеет права `0600`.

#### Scenario: config_path set to actual path

- **WHEN** `StartConfig.from_odoo_config("/worktree/odoo.conf")` вызывается
- **THEN** `config_path == "/worktree/odoo.conf"` (or `str(Path("/worktree/odoo.conf"))`), regardless of `config_path` option inside file

#### Scenario: File config_path ignored

- **WHEN** `StartConfig.from_odoo_config("/worktree/odoo.conf")` и файл содержит `config_path = /other/path`
- **THEN** `config_path` field = actual `path` argument, NOT `/other/path`

#### Scenario: Single --config in argv

- **WHEN** `_build_cli_args()` builds argv для persistent `0600` generated conf
- **THEN** ровно один `--config <path>`, no second temp config from `db_password`

#### Scenario: No temp config for 0600 persistent conf

- **WHEN** generated conf has `0600` permissions and `db_password` is set
- **THEN** `db_password` flows through the persistent config; no second temp config created

### Requirement: Database preparation result types

Database preparation and provenance comparison SHALL use frozen `msgspec.Struct` data models and stable `StrEnum` values rather than CLI dictionaries. The shared typed result SHALL include operation mode, backup identity/path/size/checksum/download time, nullable source Git branch, branch origin (`explicit`, `configured`, `unknown`), optional restored database, whether admin reset and default switch completed, prior/effective defaults, retained-artifact state, and warnings. Secrets SHALL not be fields.

Checkout provenance SHALL use the stable values `matched`, `mismatched`, and `unknown`, with expected base ref and nullable recorded branch represented explicitly.

`EnvironmentCheckoutPlan` SHALL be the public frozen typed output of `EnvironmentResource.plan_checkout()` with exactly these fields: `name: str`, `branch: str`, `effective_base_ref: str`, `db_mode: EnvironmentDatabaseMode`, `source_database: str | None`, `target_database: str | None`, `python_mode: EnvironmentPythonMode`, `provenance: BackupProvenanceComparison`, `freshness: BackupFreshness`, `preparation_actions: tuple[DatabasePreparationAction, ...]`, and `warnings: tuple[str, ...]`. It SHALL contain no config values, passwords, filesystem paths, executable/argv values, prospective UUID, or private execution plan.

`EnvironmentCheckoutResult` SHALL contain only `environment: DevelopmentEnvironment` and the final secret-free `plan: EnvironmentCheckoutPlan` recalculated after preparation. Additive `EnvironmentResource.checkout_with_plan()` SHALL return it, while canonical `checkout()` SHALL continue returning `DevelopmentEnvironment`. `commands/env.py` SHALL consume `plan_checkout()`/`checkout_with_plan()` directly for Rich and shared JSON/TOON projection; private `_CheckoutPlan` remains internal and no CLI-only checkout-plan dictionary or DTO SHALL exist. `AdminPasswordResetResult` SHALL contain the bound database, completion state, XML ID, and an optional environment ID rather than requiring an environment for refresh.

#### Scenario: Download result is adapter-neutral

- **WHEN** a download-only refresh succeeds
- **THEN** the SDK returns a frozen typed result containing backup audit/provenance fields and no CLI envelope or secret field

#### Scenario: Unknown provenance is typed

- **WHEN** a legacy backup has no source branch
- **THEN** checkout planning represents the comparison as `unknown`, not an empty string or transport-specific sentinel

#### Scenario: Checkout models are adapter-neutral

- **WHEN** dry-run plans checkout or execution completes it
- **THEN** the CLI-facing additive methods return the public plan/result models, every CLI format projects those same models without `_checkout_plan_dict`, and canonical `checkout()` still returns `DevelopmentEnvironment`

### Requirement: Backup source branch model field

`Backup` SHALL add `source_git_branch: str | None = None`. The field SHALL carry declarative source provenance from catalog through list/latest/restore lookup and serialization. Legacy rows SHALL produce `None`. It SHALL not contain a commit SHA inferred from local or remote Git state.

#### Scenario: Provenance survives restore lookup

- **WHEN** a backup with `source_git_branch="release/19"` is restored and later loaded through the restore mapping
- **THEN** the returned `Backup.source_git_branch` equals `release/19`

### Requirement: Public execution model vocabulary

The SDK SHALL publicly export lazy-loaded `Command[T]`, `ExecutionPlan`, frozen `ProcessStep`, frozen `ActionStep`, concrete plan errors, and `StalePlanError`. The public plan/value models SHALL be immutable, strictly typed, serializable through the project model boundary, and free of Expression or private executor types. `Command[T]` SHALL be immutable and strictly typed, but its private executable callback and snapshot SHALL NOT be serializable or included in project model conversion; only its public plan projection may be converted.

#### Scenario: Public execution imports

- **WHEN** a caller imports each execution model from `odoo_instance_sdk` or its canonical module
- **THEN** both imports return the same public object
- **AND** constructing or inspecting the models requires no private executor import through the package root

#### Scenario: Command model conversion is requested

- **WHEN** a caller converts public execution values through the project model boundary
- **THEN** `ExecutionPlan`, `ProcessStep`, `ActionStep`, and other public plan/value models produce serializable values
- **AND** the `Command[T]` private callback and executable snapshot are neither traversed nor emitted

### Requirement: Concrete recursive JSON values

Production model, output, and adapter annotations SHALL use a recursive `JsonValue`, concrete unions, protocols, typed mappings, or frozen structs instead of explicit `Any` or bare `object`. Untyped third-party data SHALL be validated and narrowed in one adapter before it enters production domain or output models.

#### Scenario: Untyped third-party mapping enters the SDK

- **WHEN** FastAPI, TOON, JSON, msgspec, or another weakly typed dependency returns an untyped value
- **THEN** one adapter validates it into `JsonValue` or a concrete model
- **AND** downstream annotations contain no `Any` or bare `object`

