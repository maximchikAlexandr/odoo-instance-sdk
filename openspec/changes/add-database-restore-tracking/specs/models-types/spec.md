## ADDED Requirements

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