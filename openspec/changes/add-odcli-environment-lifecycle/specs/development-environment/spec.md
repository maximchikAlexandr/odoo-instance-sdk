## ADDED Requirements

### Requirement: `DevelopmentEnvironment` public type

`DevelopmentEnvironment` MUST быть `msgspec.Struct` с `frozen=True, forbid_unknown_fields=True` и представлять provisioning record: worktree/config, reused-or-owned Python binding, port, DB ownership и cleanup audit.

Минимальные поля:

- `id: uuid.UUID`
- `name: str`
- `repository_root: str`
- `git_common_dir: str`
- `branch: str`
- `base_ref: str`
- `worktree_path: str`
- `generated_config_path: str`
- `python_environment_path: str`
- `python_environment_owned: bool`
- `dependency_lock_path: str`
- `http_interface: str`
- `http_port: int`
- `db_mode: EnvironmentDatabaseMode`
- `source_db_name: str | None`
- `target_db_name: str | None`
- `backup_id: uuid.UUID | None`
- `runtime_json: str` — versioned non-secret Odoo/Python/cwd/dependency snapshot
- `state: EnvironmentState`
- `created_at: datetime`
- `last_used_at: datetime | None`
- `removed_at: datetime | None`
- `last_error: str | None` — sanitized and length-limited

`DevelopmentEnvironment` MUST NOT содержать методов с side effects. Git/worktree/remove живут на `EnvironmentResource`, не на модели.

#### Scenario: Frozen provisioning record

- **WHEN** `DevelopmentEnvironment` constructed from catalog row
- **THEN** все поля immutable, mutation требует новой записи через `EnvironmentResource`

### Requirement: `EnvironmentState` enum

`EnvironmentState` MUST быть `enum.StrEnum` со значениями:

- `CREATING = "creating"`
- `READY = "ready"`
- `FAILED = "failed"`
- `REMOVING = "removing"`
- `CLEANUP_FAILED = "cleanup_failed"`
- `REMOVED = "removed"`

#### Scenario: State transitions

- **WHEN** checkout starts → `creating`; postconditions met → `ready`; checkout fails + full rollback → `failed`; partial cleanup → `cleanup_failed`; all owned artifacts gone → `removed`

### Requirement: `EnvironmentDatabaseMode` enum

`EnvironmentDatabaseMode` MUST быть `enum.StrEnum` со значениями:

- `SHARED = "shared"`
- `COPY = "copy"`

#### Scenario: Shared mode

- **WHEN** `db_mode = SHARED`
- **THEN** environment не владеет БД, `remove` не может `drop()` source DB

#### Scenario: Copy mode

- **WHEN** `db_mode = COPY`
- **THEN** environment владеет target DB и backup_id, `remove` drop'ит target DB

### Requirement: `EnvironmentEvent` public type

`EnvironmentEvent` MUST быть `msgspec.Struct` с `frozen=True, forbid_unknown_fields=True`:

- `sequence: int`
- `environment_id: uuid.UUID`
- `operation: str` — `checkout|sync|use|shell|remove`
- `outcome: str` — `started|succeeded|failed`
- `occurred_at: datetime`
- `message: str | None` — optional sanitized

Exact ownership живёт в environment columns, а не в event taxonomy.

#### Scenario: Append-only event

- **WHEN** checkout succeeds
- **THEN** `environment_events` получает row `operation=checkout, outcome=succeeded`; row never deleted

### Requirement: `EnvironmentCheckoutOptions` public type

`EnvironmentCheckoutOptions` MUST быть `msgspec.Struct` с `frozen=True`:

- `base_ref: str | None = None`
- `name: str | None = None`
- `config_path: Path | None = None`
- `db_mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.SHARED`
- `source_database: str | None = None`
- `target_database: str | None = None`
- `odoo_bin: Path | None = None`
- `python: str | Path | None = None`
- `create_venv: bool = False`
- `http_port: int | None = None`

`create_venv` default `false` и не может прийти из project manifest, VS Code profile или cwd inference: только explicit `--create-venv` текущего checkout.

#### Scenario: Default shared checkout

- **WHEN** `EnvironmentCheckoutOptions()` используется без изменений
- **THEN** `db_mode=SHARED`, `create_venv=False`

### Requirement: `EnvironmentResource` public API

`EnvironmentResource` MUST быть exposed как `OdooClient.environments` и предоставлять:

```python
def checkout(
    self,
    project: ProjectConfig | Path,
    branch: str,
    *,
    options: EnvironmentCheckoutOptions = EnvironmentCheckoutOptions(),
) -> DevelopmentEnvironment: ...

def sync_python(
    self,
    selector: EnvironmentSelector,
    *,
    upgrade: bool = False,
) -> DevelopmentEnvironment: ...

def get(self, selector: EnvironmentSelector) -> DevelopmentEnvironment: ...

def list(
    self,
    *,
    project: ProjectConfig | Path | None = None,
    include_removed: bool = False,
    verify: bool = False,
) -> list[DevelopmentEnvironment]: ...

def history(self, selector: EnvironmentSelector) -> list[EnvironmentEvent]: ...

def remove(self, selector: EnvironmentSelector) -> None: ...
```

`EnvironmentSelector = str | DevelopmentEnvironment` — UUID or exact name; ambiguity is an error.

Git/`uv`/`fcntl.flock`/generated config MUST оставаться internal implementation `EnvironmentResource`, не public module.

#### Scenario: Checkout returns DevelopmentEnvironment

- **WHEN** `client.environments.checkout(project, "feat/x")` succeeds
- **THEN** возвращается `DevelopmentEnvironment` со `state=READY`

#### Scenario: Selector ambiguity is error

- **WHEN** `client.environments.get("feat")` matches 2 environments by name
- **THEN** поднимается `EnvironmentConflictError` с details

### Requirement: Prohibited public types

SDK MUST NOT добавлять public:

- `GitWorktree`
- `PythonVenv`
- `LockManager`
- `ModuleResource`
- `TranslationResource`
- environment-specific process wrapper
- interfaces/factories/repositories для единственной SQLite-реализации
- второй catalog file

Catalog остаётся internal persistence primitive; resource возвращает typed `msgspec.Struct` models.

#### Scenario: No public GitWorktree

- **WHEN** user imports `odoo_instance_sdk`
- **THEN** `GitWorktree` не доступен; worktree управляется только через `EnvironmentResource`

### Requirement: `OdooClient.environments` facade

`OdooClient` MUST expose `environments: EnvironmentResource` наравне с `instance` и `backups`. Catalog открывается internally и не экспортируется как `client.catalog`.

```text
OdooClient
├── instance          # InstanceFactory
├── backups           # BackupResource
└── environments      # EnvironmentResource
```

#### Scenario: Three facades

- **WHEN** `OdooClient` constructed
- **THEN** `client.instance`, `client.backups`, `client.environments` доступны; `client.catalog` отсутствует

### Requirement: Public errors

Public errors для environment operations:

- existing `ConfigError`
- `EnvironmentNotFoundError`
- `EnvironmentConflictError(code, details)`

Selector не выбирается по recency и не выбирается по «единственному ready».

#### Scenario: Environment not found

- **WHEN** `client.environments.get(uuid)` для несуществующего ID
- **THEN** `EnvironmentNotFoundError`

#### Scenario: Environment conflict

- **WHEN** checkout для repo+branch с уже active environment
- **THEN** `EnvironmentConflictError` с code и details