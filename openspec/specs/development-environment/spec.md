## Purpose

Provisioning, ownership, and cleanup of isolated development environments bound to a Git worktree, Python interpreter, generated Odoo config, and catalog audit.
## Requirements
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

### Requirement: `EnvironmentCheckoutOptions` public type

`EnvironmentCheckoutOptions` MUST be a `msgspec.Struct` with `frozen=True` and these fields:

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
- `hash_lock: Path | None = None`
- `hash_lock_sha256: str | None = None`

`base_ref` is the explicit per-call override; when it is `None`, checkout SHALL use `ProjectConfig.default_base_ref`, then `HEAD`. `source_database is not None` SHALL record explicit caller intent for the legacy-unknown provenance exception even when it equals the configured project default.

`create_venv` SHALL default to `false` and SHALL NOT come from a project manifest, VS Code profile, or cwd inference; only explicit `--create-venv` on the current checkout enables it. `hash_lock` and `hash_lock_sha256` SHALL default to `None`, SHALL be accepted only as a pair with `create_venv=True`, and SHALL NOT come from a manifest, profile, environment variable, or cwd inference. Checkout SHALL validate the pair, canonical regular-file path, lowercase SHA-256, and matching file bytes while constructing its immutable command and SHALL revalidate the digest immediately before the dependency process step.

#### Scenario: Default shared checkout

- **WHEN** `EnvironmentCheckoutOptions()` is used unchanged and the project has no default base
- **THEN** `db_mode=SHARED`, `create_venv=False`, `hash_lock=None`, `hash_lock_sha256=None`, and the effective base SHALL be `HEAD`

#### Scenario: Explicit source records legacy opt-in

- **WHEN** options explicitly contain `source_database="legacy_db"`
- **THEN** checkout MAY apply the warned unknown-provenance exception for that exact database

#### Scenario: Checkout hash-lock validation fails closed

- **WHEN** checkout receives only one paired input, a malformed or mismatched digest, a non-regular/unreadable path, or hash-lock inputs without `create_venv=True`
- **THEN** command construction SHALL fail before Git, uv, filesystem, catalogue, database, or environment mutation

#### Scenario: Checkout dry-run uses the immutable hash-lock plan

- **WHEN** public checkout dry-run receives a valid lock pair with `create_venv=True`
- **THEN** it SHALL expose the sanitized captured `uv pip sync --require-hashes` step and matching lock digest without spawning a process or changing any resource
- **THEN** later execution of that captured command SHALL consume the same argv, cwd, digest, and action order rather than rebuilding them

### Requirement: `EnvironmentResource` public API

`EnvironmentResource` MUST be exposed as `OdooClient.environments` and provide:

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
    hash_lock: Path | None = None,
    hash_lock_sha256: str | None = None,
) -> DevelopmentEnvironment: ...

def sync_python_command(
    self,
    selector: EnvironmentSelector,
    *,
    upgrade: bool = False,
    hash_lock: Path | None = None,
    hash_lock_sha256: str | None = None,
) -> Command[DevelopmentEnvironment]: ...

def get(self, selector: EnvironmentSelector) -> DevelopmentEnvironment: ...

def list(
    self,
    *,
    project: ProjectConfig | Path | None = None,
    include_removed: bool = False,
) -> list[DevelopmentEnvironment]: ...

def remove(self, selector: EnvironmentSelector) -> None: ...
```

Selector SHALL be a UUID or exact name (`str | DevelopmentEnvironment`); ambiguity SHALL be an error. `history()` and `list(verify=)` SHALL NOT enter the public API. Events SHALL remain in the catalogue and `doctor` SHALL read them internally. Git, uv, `fcntl.flock`, hash/digest validation, and generated configuration SHALL remain internal implementation of `EnvironmentResource`, not a public module.

`sync_python()` SHALL delegate to `sync_python_command(...).run()` with the same arguments. Checkout convenience methods SHALL consume the command captured from their `EnvironmentCheckoutOptions`; no public convenience method or CLI adapter SHALL rebuild hash-lock argv, cwd, environment, digest evidence, or action order.

#### Scenario: Checkout returns DevelopmentEnvironment

- **WHEN** `client.environments.checkout(project, "feat/x")` succeeds
- **THEN** it SHALL return a `DevelopmentEnvironment` with `state=READY`

#### Scenario: Selector ambiguity is error

- **WHEN** `client.environments.get("feat")` matches two environments by name
- **THEN** it SHALL raise `EnvironmentConflictError` with details

#### Scenario: Sync convenience and command signatures agree

- **WHEN** callers pass the same selector, upgrade value, and hash-lock pair to `sync_python()` and `sync_python_command()`
- **THEN** both SHALL represent the same public operation and `sync_python()` SHALL execute the exact command snapshot returned by its command sibling

#### Scenario: Legacy public calls retain defaults

- **WHEN** an existing caller omits both new keyword arguments or constructs default checkout options
- **THEN** source compatibility and existing dependency behavior SHALL be preserved with both hash-lock values equal to `None`

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

`EnvironmentResource.list()` остаётся источником environment rows для SDK callers. `EnvironmentMonitor` reads `BackupCatalog.list_environments` / `list_environment_runtimes` directly (via `get_catalog_path()` or injected `catalog_path`) and MUST NOT reimplement catalog schema or scan the filesystem. `odcli env list` SHALL call `EnvironmentResource.checkout_inventory_command()` (or its `checkout_inventory()` convenience), which delegates to the monitor projection without duplicating snapshot logic. `odcli monitor` and `odcli ps` consume raw `EnvironmentMonitor.snapshot()` / `processes_command()` respectively. `EnvironmentResource` does not grow runtime methods beyond the checkout-inventory delegate; `environment_runtime` is catalog-internal.

#### Scenario: Three facades

- **WHEN** `OdooClient` constructed
- **THEN** `client.instance`, `client.backups`, `client.environments` доступны; `client.catalog` отсутствует

### Requirement: Public errors

Public errors для environment operations MUST be limited to:

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

### Requirement: Catalog current-runtime record (schema v8 → v9)

Catalog MUST хранить одну current runtime-запись на environment в таблице `environment_runtime`. The first Alembic revision SHALL create this table as part of the complete current schema. Sequential `PRAGMA user_version` v8→v9 and `CURRENT_SCHEMA_VERSION = 9` SHALL NOT remain as a production migration ledger.

`BackupCatalog` MUST предоставлять read-only `list_environments_with_runtimes()` returning each environment and its current runtime from one SQLite read snapshot using two SELECTs in that transaction, plus `get_environment_runtime()` and `list_environment_runtimes()` for their explicit read-only callers, and write `upsert_environment_runtime(...)` / `clear_environment_runtime(environment_id)` (только из `run_foreground` and the detached launch command that persists runtime identity).

Collector (`EnvironmentMonitor`) reads runtime rows read-only. PID safety: collector считает process живым только при `psutil.Process(pid).create_time() == recorded_create_time` и `psutil.pid_exists(pid)`; mismatch → `runtime.state="stopped"`.

#### Scenario: Migration adds runtime table

- **WHEN** a fresh catalogue is created or a known alpha catalogue is stamped
- **THEN** `environment_runtime` table exists, environments without a live process have no runtime row, and no `PRAGMA user_version` step runs

#### Scenario: Upsert is one-row-per-environment

- **WHEN** `upsert_environment_runtime(env_id, ...)` is called twice for the same environment
- **THEN** one row exists with the latest values (no duplicates)

#### Scenario: Collector reads runtime read-only

- **WHEN** `EnvironmentMonitor.snapshot()` runs
- **THEN** its aggregate catalog read is read-only; collector never calls `upsert`/`clear`

#### Scenario: Collector reads one aggregate snapshot

- **WHEN** `EnvironmentMonitor.snapshot()` runs
- **THEN** it calls `list_environments_with_runtimes()` once and never calls `upsert`/`clear`

### Requirement: Operation locks

SDK MUST NOT держать SQLite transaction во время Git/uv/Odoo/DB operations. На Unix MUST использоваться stdlib `fcntl.flock(fd, LOCK_SH|LOCK_EX|LOCK_NB)` над deterministic files в `user_state_dir("odoo-instance-sdk")/locks`:

- один catalog-migration lock;
- project+branch provisioning lock для checkout до появления environment ID;
- один per-environment lock.

`run` и interactive shell получают `LOCK_SH` (shared readers). `sync` и `remove` — `LOCK_EX` (exclusive writers). Conflict — fail-fast. Kernel освобождает lock при normal exit и SIGKILL, поэтому stale-lock protocol, break command, PID recovery не нужны; lock file может оставаться как безвредный inode.

Lock files и `flock` MUST быть internal implementation `EnvironmentResource`/instance runtime, не public module и не CLI `LockManager`. CLI MUST NOT acquire or call lock API; locks берутся внутри SDK methods (`checkout`/`sync_python`/`remove`/`run_foreground`/`shell`).

Lock защищает только SDK-managed artifacts, не PostgreSQL transactions или external processes. Windows locking откладывается до фактического требования поддержки Windows.

#### Scenario: Exclusive lock for checkout

- **WHEN** checkout acquires project+branch provisioning lock
- **THEN** concurrent checkout для same repo+branch fails fast (LOCK_NB)

#### Scenario: Shared lock for run

- **WHEN** `odcli run` acquires per-environment lock
- **THEN** lock is `LOCK_SH`; concurrent `run`/`shell` allowed; concurrent `sync`/`remove` fail-fast

#### Scenario: Auto-release on SIGKILL

- **WHEN** process holding lock killed by SIGKILL
- **THEN** kernel releases lock; no stale-lock protocol needed

### Requirement: Checkout preflight

До любых изменений `checkout` MUST:

1. Найти repository root и git common dir через Git CLI.
2. Проверить `git`, `uv`, ref/branch, config/Odoo paths и Python mode. Default требует существующий venv interpreter; без него checkout ошибается с подсказкой `--create-venv`.
3. Проверить, что active environment для этой пары repository + branch ещё не существует.
4. Разрешить source DB и target DB до создания артефактов.
5. Для `copy` проверить локальность source instance, master password и отсутствие target DB.

Dirty основной checkout MUST NOT блокировать создание worktree и MUST NOT изменяться командой.

#### Scenario: Missing venv interpreter

- **WHEN** default checkout не находит existing venv interpreter
- **THEN** checkout ошибается с подсказкой `--create-venv`

#### Scenario: Dirty main checkout не блокирует

- **WHEN** main checkout имеет uncommitted changes
- **THEN** worktree создаётся, main checkout не изменяется

#### Scenario: Active environment already exists

- **WHEN** checkout для repo+branch, где уже active environment
- **THEN** `EnvironmentConflictError`

### Requirement: Worktree placement

Worktree MUST храниться в пользовательском data directory:

```text
<platformdirs.user_data_dir("odoo-instance-sdk")>/environments/
└── <repo-key>/
    └── <environment-id>/
        ├── worktree/
        ├── venv/              # only with --create-venv
        ├── requirements.lock
        └── odoo.conf
```

`repo-key` MUST включать безопасный slug и короткий hash от canonical git common dir, чтобы одинаковые имена репозиториев не конфликтовали.

#### Scenario: Repo-key collision avoidance

- **WHEN** два разных репозитория с одинаковым именем "odoo-project"
- **THEN** их `repo-key` отличаются из-за hash от canonical git common dir

### Requirement: Worktree branch rules

Worktree branch handling MUST follow these rules:

- Существующая локальная branch подключается через `git worktree add`.
- Единственная подходящая remote branch создаёт tracking branch.
- Отсутствующая branch создаётся от `--base`.
- Branch, уже checkout-нутая в другом worktree, вызывает понятную ошибку.
- Никакого `--force`, `-B`, reset или удаления существующего worktree.
- Состояние читается через стабильный `git worktree list --porcelain -z`.

#### Scenario: Existing local branch

- **WHEN** checkout branch `feat/x`, которая существует locally
- **THEN** `git worktree add` подключает существующую branch

#### Scenario: Branch checked out elsewhere

- **WHEN** checkout branch `feat/x`, уже checked out в другом worktree
- **THEN** понятная error, no `--force`

### Requirement: System Git via subprocess

Использовать системный Git через `subprocess.run([...], shell=False)`. GitPython, Dulwich и pygit2 для этого scope не нужны. Git CLI, porcelain parsing и worktree paths MUST NOT экспортироваться как public module: это internal adapter `EnvironmentResource`.

#### Scenario: No shell invocation

- **WHEN** `EnvironmentResource` вызывает Git
- **THEN** `subprocess.run` с `shell=False`, args как list

### Requirement: Generated `odoo.conf`

Исходный config MUST NEVER изменяться. Производный config MUST записываться атомарно с правами `0600` и сохранять все неизвестные options.

Обязательные изменения:

- элементы `addons_path` и `upgrade_path`, находящиеся внутри исходного repository root, rebased на worktree;
- внешние пути к Odoo core/addons остаются без изменений;
- `http_interface` по умолчанию становится `127.0.0.1`;
- `http_port` берётся из environment registry;
- `db_name` становится source DB в `shared` mode и target DB в `copy` mode;
- `dbfilter` ограничивается выбранной БД;
- DB connection settings, `admin_passwd` и `data_dir` сохраняются;
- если source config содержит непустой `logfile`, generated config MUST переписать его в environment-owned абсолютный path рядом с generated conf (`<env-root>/odoo.log`); если `logfile` отсутствует или пуст — поведение сохраняется (ключ не добавляется);
- CLI не добавляет собственный log capture или tee и MUST NOT создавать сам log file.

Для MVP достаточно stdlib `configparser`, `pathlib`, `shutil`, `tempfile` и `os.replace`. Комментарии generated copy могут не сохраняться; неизвестные keys и values MUST сохраняться.

#### Scenario: Atomic 0600 config

- **WHEN** generated config записывается
- **THEN** atomic write (`os.replace`), права `0600`, исходный config не изменяется

#### Scenario: Source logfile rewritten to env-owned path

- **WHEN** source `odoo.conf` contains `logfile = /tmp/shared.log`
- **THEN** generated config has an absolute `logfile` under the environment root and the source file is unchanged; the log file itself is not created

#### Scenario: Absent logfile preserved

- **WHEN** source `odoo.conf` has no `logfile`
- **THEN** generated config also has no `logfile`

#### Scenario: Repo-local addons rebased

- **WHEN** `addons_path` содержит repo-local entry `./addons`
- **THEN** generated config содержит rebased path внутри worktree

#### Scenario: External Odoo core preserved

- **WHEN** `addons_path` содержит external `/opt/odoo/addons`
- **THEN** generated config сохраняет `/opt/odoo/addons` без изменений

### Requirement: DB name validation for copy

Для любого `copy` source/target DB name MUST быть безопасным Odoo filestore component:

- UTF-8 length ≤63 bytes;
- regex `[A-Za-z0-9_][A-Za-z0-9_.-]*`;
- не `.`/`..`;
- slash, backslash, NUL и absolute/path syntax запрещены.

До DB/filesystem mutation SDK MUST canonicalize exact `<data_dir>/filestore/<db-name>`, доказать containment под resolved filestore root и отсутствие escaping symlinks. Эти проверки дополняют PostgreSQL identifier quoting.

#### Scenario: Valid DB name

- **WHEN** target DB name `comerta_cmrt_123`
- **THEN** validation passes

#### Scenario: Path traversal blocked

- **WHEN** target DB name `../etc/passwd`
- **THEN** validation rejects, checkout aborts

### Requirement: `shared` DB mode

`shared` mode MUST follow these rules:

- backup и restore не выполняются;
- generated config указывает на исходную БД;
- environment не владеет этой БД;
- `remove` не имеет права вызывать `drop()` для исходной БД;
- результат явно предупреждает, что код/process изолированы, а БД и filestore — нет.

#### Scenario: Shared checkout

- **WHEN** `checkout --db-mode shared`
- **THEN** generated config → source DB, `backup_id=None`, `target_db_name=None`, warning о неизолированной БД

### Requirement: `copy` DB mode

`copy` mode MUST:

1. Создать отдельный ZIP backup source DB с filestore через существующий `backup()`.
2. Сохранить `backup_id` как принадлежащий этому environment.
3. Восстановить target DB в том же cluster через `restore(..., copy=True, neutralize_database=True)`.
4. Проверить postcondition `exists(target_db) is True`.
5. Только после этого переключить environment в `ready`.

Source Odoo HTTP endpoint MUST быть локальным и доступным. При недоступном endpoint checkout завершается понятной ошибкой и оставляет аудируемое `failed` environment. Target DB NEVER перезаписывается и не удаляется для повторной попытки автоматически.

#### Scenario: Copy checkout success

- **WHEN** `checkout --db-mode copy --source-db comerta --target-db comerta_x`
- **THEN** backup создаётся, `backup_id` записан, target DB restored, postcondition `exists(comerta_x) is True`, state `ready`

#### Scenario: Source Odoo unavailable

- **WHEN** `checkout --db-mode copy` и source Odoo HTTP недоступен
- **THEN** checkout завершается error, environment остаётся `failed`, удаляется повторяемым `remove`

#### Scenario: Remote source Odoo refused

- **WHEN** `checkout --db-mode copy` и source Odoo HTTP доступен но remote (non-local endpoint)
- **THEN** checkout завершается error (local-only constraint), environment остаётся `failed`

### Requirement: `--source-db` inference

`--source-db NAME` inferred only when `odoo.conf` contains exactly one `db_name`. Если `odoo.conf` содержит multiple `db_name` (comma-separated) и `--source-db` не указан явно, checkout MUST завершаться error с подсказкой указать `--source-db`.

#### Scenario: Single db_name inferred

- **WHEN** `odoo.conf` содержит `db_name = comerta` и `--source-db` не указан
- **THEN** `source_db_name = "comerta"` inferred

#### Scenario: Multiple db_names without flag — error

- **WHEN** `odoo.conf` содержит `db_name = comerta,test` и `--source-db` не указан
- **THEN** checkout error с подсказкой указать `--source-db`

#### Scenario: Empty db_name in copy mode without flag — error

- **WHEN** `odoo.conf` не содержит `db_name` (или пустой) и `checkout --db-mode copy` без `--source-db`
- **THEN** checkout error с подсказкой указать `--source-db`

### Requirement: `--target-db` safe default

`--target-db NAME` default: safe `<source>_<branch>_<short-hash>`. Default name MUST pass DB name validation (requirement выше).

#### Scenario: Auto-generated target name

- **WHEN** `checkout --db-mode copy --source-db comerta` для branch `feat/CMRT-123` без `--target-db`
- **THEN** `target_db_name` = safe slug из source, branch и short-hash, passes validation

### Requirement: Checkout creates environment in `creating` before artifacts

`DevelopmentEnvironment` row MUST создаваться в catalog со `state=creating` ДО создания первого owned artifact. Exact owned paths/names MUST фиксироваться в columns до создания.

При checkout failure выполняется best-effort cleanup только уже созданных и доказанно owned artifacts:

- Если rollback полный → environment остаётся `failed` только как audit row.
- Если неполный → `cleanup_failed` и виден в обычном `list`.

Повторный checkout для уже active repo+branch MUST возвращать существующее matching environment либо `EnvironmentConflictError`; дубликат не создаётся.

#### Scenario: Environment row before worktree

- **WHEN** checkout starts
- **THEN** `environments` row created with `state=creating`, exact paths fixed in columns, BEFORE `git worktree add`

#### Scenario: Full rollback on failure

- **WHEN** checkout fails и все created owned artifacts successfully cleaned up
- **THEN** environment state = `failed`, audit row remains

#### Scenario: Partial rollback → cleanup_failed

- **WHEN** checkout fails и some owned artifacts cannot be cleaned up
- **THEN** environment state = `cleanup_failed`, visible in `env list`, retryable `remove`

### Requirement: Port auto-allocation during checkout

Если `--http-port` не указан, checkout MUST автоматически выбирать свободный port:

1. `socket.bind((http_interface, 8069))` — попробовать preferred_http_port из project manifest (если set), иначе Odoo default 8069.
2. Если занят — инкрементировать port и retry (8070, 8071, ...). Range: 8069–8099 (31 port). Если весь range занят → `EnvironmentConflictError` с подсказкой указать `--http-port` явно.
3. Выбранный port MUST быть уникальным среди active environments (constraint в catalog).
4. Свободный port автоматически выбирается только во время checkout до первого запуска.

Port registry не гарантирует OS-level reservation между checkout и run. Повторная `socket.bind()`-проверка перед process start обязательна.

#### Scenario: Auto-allocated port

- **WHEN** `checkout` без `--http-port` и port 8069 свободен
- **THEN** `http_port = 8069` записан в environment

#### Scenario: Auto-increment on occupied port

- **WHEN** `checkout` без `--http-port` и port 8069 занят, 8070 свободен
- **THEN** `http_port = 8070` записан в environment

#### Scenario: Auto-allocated port must be unique

- **WHEN** `checkout` без `--http-port` и выбранный port уже allocated to another active environment
- **THEN** checkout пробует следующий port; `EnvironmentConflictError` если все заняты

### Requirement: Python environment reuse

Default checkout переиспользует interpreter из project manifest/`--python`. Он MUST exist и report virtual-env prefix; его location может быть external, он регистрируется как `owned=false` и NEVER удаляется SDK.

Только `--create-venv` выполняет `uv venv <environment-root>/venv --python <selector>` и регистрирует artifact как `owned=true`.

`create_venv` default всегда `false` и не может прийти из project manifest, VS Code profile или cwd inference: только explicit `--create-venv` текущего checkout.

#### Scenario: Reuse existing venv

- **WHEN** default checkout с existing project venv
- **THEN** `python_environment_owned=false`, venv never deleted by SDK

#### Scenario: Create owned venv

- **WHEN** `checkout --create-venv --python 3.12`
- **THEN** `uv venv` создаёт venv under environment root, `python_environment_owned=true`

### Requirement: Dependency compilation

Without explicit hash-lock inputs, Odoo Core and project requirements in both Python modes SHALL be compiled by one `uv pip compile` into the environment-owned `requirements.lock`.

During checkout, `uv pip compile` SHALL receive `--python <target-python>`, where `<target-python>` is the exact resolved interpreter passed to the immediately following dependency installation step: the recorded project interpreter for a reused environment or the interpreter inside the newly created owned venv. The immutable checkout plan and its dry-run projection SHALL expose this same target without resolving or rebuilding it at execution time.

- reused venv: `uv pip install --python <project-python> -r <lock>` SHALL preserve unrelated project tools;
- owned venv: `uv pip sync --python <environment-python> <lock>` SHALL provide isolation;
- uv writes SHALL be serialized by `flock` on the canonical Python-environment path;
- repository-local dependency files SHALL be rebased into the worktree, and the lock/fingerprint SHALL refer to that worktree;
- `env sync --upgrade` SHALL update pins, regular sync SHALL preserve them, and a failed compile SHALL NOT replace a valid lock;
- `run` and `shell` SHALL NOT call `sync_python`; only checkout and `env sync` SHALL write the lock/venv, while `doctor` and `deps verify` report drift.

When paired `hash_lock` and `hash_lock_sha256` inputs are supplied for an owned environment, checkout and `env sync` SHALL replace the discovery/compile branch with the hash-verified `uv pip sync --require-hashes` branch defined below. Hash-lock mode SHALL NOT rewrite the reviewed lock or fall back to compilation or installation.

Runtime prefix SHALL always be `[recorded-python, odoo-bin]`. `uv venv`, dependency argv construction, execution, and fingerprinting SHALL remain internal implementation of the existing environment resource rather than a new public venv module.

#### Scenario: Checkout compiles for reused Python

- **WHEN** checkout reuses an existing project virtual environment and compiles discovered dependency inputs
- **THEN** its compile and install steps SHALL both contain `--python` with the exact recorded project interpreter

#### Scenario: Checkout compiles for owned Python

- **WHEN** checkout creates an owned virtual environment and compiles discovered dependency inputs
- **THEN** its compile and sync steps SHALL both contain `--python` with the exact interpreter inside that owned environment

#### Scenario: Failed compile keeps valid lock

- **WHEN** `uv pip compile` fails in legacy mode and a valid `requirements.lock` already exists
- **THEN** the valid lock SHALL NOT be replaced and synchronization SHALL continue with that lock

#### Scenario: Hash-lock mode bypasses compilation by contract

- **WHEN** valid paired hash-lock inputs select an owned environment
- **THEN** dependency discovery and compile SHALL be absent from the immutable plan and the reviewed lock SHALL remain byte-identical

### Requirement: `env sync`

`env sync [ENVIRONMENT] [--upgrade] [--hash-lock PATH --hash-lock-sha256 SHA256]` MUST:

- preserve pins during regular synchronization and update them only with `--upgrade`;
- rebase repository-local dependency files into the worktree;
- serialize uv writes with `flock`;
- require `--hash-lock` and `--hash-lock-sha256` together, require a lowercase 64-hex digest and a regular readable file, and reject hash-lock mode with `--upgrade` or a reused/non-owned Python environment;
- in hash-lock mode, verify the exact file digest before mutation and capture `uv pip sync --python <owned-python> --require-hashes <canonical-lock>` through the existing immutable command/process boundary;
- in hash-lock mode, perform no Odoo/project requirement discovery, `uv pip compile`, or `uv pip install` fallback;
- expose the same paired inputs and semantics to `EnvironmentResource.sync_python()`, `sync_python_command()`, public `env sync`, and `env checkout --create-venv`, with the convenience methods delegating to the captured command rather than rebuilding it;
- preserve the existing compile/install behavior when both hash-lock inputs are absent.

#### Scenario: Sync upgrade

- **WHEN** `env sync --upgrade` runs without hash-lock inputs
- **THEN** pins SHALL be updated in `requirements.lock`

#### Scenario: Owned environment consumes an audited hash lock

- **WHEN** public checkout or `env sync` receives a regular lock file and its matching SHA-256 for an OdCLI-owned environment
- **THEN** the immutable plan SHALL contain exactly one hash-enforced dependency command using the owned interpreter and canonical lock path
- **THEN** execution SHALL use `uv pip sync --require-hashes` without discovery, compilation, or an install fallback

#### Scenario: Hash-lock validation fails before mutation

- **WHEN** either paired input is missing, the digest is malformed or mismatched, the lock is not a regular readable file, `--upgrade` is also supplied, or the environment is not owned
- **THEN** the operation SHALL fail before spawning uv or changing the environment lock, catalogue evidence, or installed packages

#### Scenario: Legacy synchronization remains compatible

- **WHEN** neither hash-lock input is supplied
- **THEN** checkout and `env sync` SHALL retain their existing discovery, compile, owned-sync, and reused-environment install behavior

### Requirement: `env list`

`env list` MUST provide the following invocations and output rules:

```bash
odcli env list
odcli env list --all
odcli env list --format rich|json|toon
odcli env list --all-projects
odcli env list --watch [--interval SECONDS]
```

The command SHALL project one frozen `CheckoutInventory` model for Rich, JSON, and TOON. The main checkout of each selected project SHALL appear as the first typed row of its group with `kind = main | environment`, a stable `project_id`, and a nullable `environment_id`. The main checkout SHALL NOT be modelled as a synthetic environment.

The base row SHALL contain only working identity and state: kind/name and project; branch, short SHA, and canonical worktree path; commits ahead of the base branch plus added and deleted lines; a compact Odoo status `running | stopped | unavailable` without PID or metrics; and the bound database/DB mode when applicable.

Rich SHALL NOT show `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, `SIZE`, or detailed process/artifact columns; those values live in `odcli ps`. Rich SHALL remain a readable `Table` with headers and checkout rows on both normal and compact terminal widths. Quick reconciliation columns (`OBSERVED`, port state, owned backup) SHALL NOT appear in `env list`; they are available in `odcli ps` and `odcli doctor`.

По умолчанию скрываются только `removed`; `failed` и `cleanup_failed` видны.

`--all-projects` (CLI-level flag, см. `cli-odcli` spec) читает durable global registry из любой directory и не требует project context.

`--all` — include `removed` environments (по умолчанию скрыты).

`CheckoutInventory` SHALL be built from one canonical `EnvironmentMonitor.snapshot()` per sample plus Git facts of the main checkout. A separate monitor or collector SHALL NOT be added. The raw `EnvironmentMonitor.snapshot()` SHALL remain the canonical source for `odcli ps`, the Python SDK, FastAPI, and the dashboard.

#### Scenario: Default hides removed

- **WHEN** `env list` без `--all`
- **THEN** `removed` environments скрыты, `failed`/`cleanup_failed` видны

#### Scenario: Main checkout is the first row

- **WHEN** `env list` runs for a project with one environment
- **THEN** the first row has `kind=main` and no synthetic environment is created

#### Scenario: Rich drops process columns

- **WHEN** `env list` renders a Rich table
- **THEN** the columns `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, and `SIZE` are absent

#### Scenario: Stopped checkout stays visible

- **WHEN** `env list` runs and the main checkout's Odoo is stopped
- **THEN** the main checkout row remains visible with `stopped` status and no PID

#### Scenario: Reconciliation detects missing worktree

- **WHEN** `env list` runs for an environment where the worktree is missing
- **THEN** the `CheckoutInventory` row reflects the missing worktree in its compact status without a separate `OBSERVED` column

#### Scenario: OBSERVED reflects live socket.bind

- **WHEN** `env list` runs for an environment with an allocated port and `socket.bind((http_interface, http_port))` succeeds
- **THEN** the compact Odoo status reflects the live port state without a dedicated `OBSERVED` column

#### Scenario: Reconciliation detects missing generated config

- **WHEN** `env list` runs for an environment where the generated `odoo.conf` is missing
- **THEN** the `CheckoutInventory` row reflects the missing config in its compact status

#### Scenario: Reconciliation detects missing owned backup

- **WHEN** `env list` runs for a copy environment where the owned backup file is missing
- **THEN** the `CheckoutInventory` row reflects the missing backup in its compact status

#### Scenario: Reconciliation detects missing Python or lock

- **WHEN** `env list` runs for an environment where the recorded Python path does not exist or `requirements.lock` is missing
- **THEN** the `CheckoutInventory` row reflects the missing Python/lock in its compact status

### Requirement: `env remove`

```bash
odcli env remove <environment-id> --dry-run
odcli env remove <environment-id> --yes
odcli env remove <env-id-1> <env-id-2> --dry-run
odcli env remove <env-id-1> <env-id-2> --yes
```

Перед изменениями показать план и выполнить полный preflight. Без `--yes` требуется Click confirmation.

`env remove` SHALL accept variadic positional arguments (full UUIDs or selectors). For each explicit target, the persisted repository, Git common dir, worktree, and PostgreSQL cluster SHALL be resolved independently; the project/cluster of the current cwd SHALL NOT be applied to the whole set. A call without a positional argument SHALL preserve the existing cwd semantics for exactly one environment.

Before the first destructive action, the command SHALL resolve all targets and perform planning preflight. An unknown, ambiguous, or duplicate target SHALL abort with no changes. After successful preflight, Rich SHALL show one confirmation listing all sanitized targets; machine modes without `--yes` SHALL change nothing and SHALL emit `confirmation_required`. Execution SHALL run single-target commands sequentially in argument order with per-target execution-time revalidation. A per-target failure SHALL continue remaining prepared targets and return per-target success/failure with a non-zero exit code.

Default cleanup matrix (per target):

| Artifact | `shared` | `copy` |
|---|---:|---:|
| Generated config | delete | delete |
| Requirements lock | delete | delete |
| Python venv | delete iff owned | delete iff owned |
| Owned Git worktree | remove | remove |
| Source DB | never | never |
| Target DB | n/a | drop |
| Environment backup | n/a | delete |
| Git branch | keep | keep |
| Audit rows | keep | keep |

Safety rules:

- сначала проверить, что worktree чистый; dirty worktree блокирует удаление;
- любой занятый reserved address блокирует удаление как ownership-unknown; занятость определяется через `socket.bind((http_interface, http_port))`; HTTP health check служит только диагностикой, не доказательством ownership; responsive Odoo на address не доказывает, что он принадлежит этому environment;
- drop target DB (copy mode only) MUST быть только для `copy` environment с совпавшими cluster identity, target DB и recorded restore/backup ownership; после drop MUST проверять postcondition `exists(target_db) is False`; если postcondition fails — `cleanup_failed` с причиной;
- использовать `git worktree remove`, не recursive filesystem deletion;
- generated lock удалять по recorded environment path; Python venv — только при `python_environment_owned=true` и containment внутри environment root;
- reused project venv (`owned=false`) никогда не изменять во время remove;
- не использовать Git force и не удалять branch;
- shared source DB не удаляется ни при каких flags;
- `BackupResource.delete()` используется только для recorded environment-owned backup;
- отсутствие уже удалённого owned artifact считается идемпотентным успехом и записывается в audit;
- частичная ошибка оставляет `cleanup_failed` с точной причиной; повторный `remove` продолжает с оставшихся owned artifacts;
- `removed` ставится только после подтверждения отсутствия всех owned artifacts;
- final empty environment directory удаляется, SQLite rows остаются.

Bulk prune, автоматическое удаление по возрасту и `--force` для грязных worktrees не входят в scope. A generic bulk SDK, parallel deletion, or new orchestration hierarchy SHALL NOT be added.

#### Scenario: Dirty worktree blocks remove

- **WHEN** `env remove` для environment с dirty worktree
- **THEN** remove блокируется, error

#### Scenario: Occupied port blocks remove

- **WHEN** `env remove` и `socket.bind((http_interface, http_port))` fails (port occupied)
- **THEN** remove блокируется как ownership-unknown; HTTP response только diagnostic

#### Scenario: Shared source DB never dropped

- **WHEN** `env remove` для `shared` environment
- **THEN** source DB не удаляется ни при каких flags

#### Scenario: Drop postcondition checked

- **WHEN** `env remove` для `copy` environment, target DB drop succeeds
- **THEN** postcondition `exists(target_db) is False` verified; if fails → `cleanup_failed`

#### Scenario: Drop refused on cluster identity mismatch

- **WHEN** `env remove` для `copy` environment, но target DB cluster identity не совпадает с recorded (e.g. DB moved to different cluster) OR no recorded restore/backup ownership
- **THEN** drop refused, `cleanup_failed` с причиной; target DB не удаляется

#### Scenario: Idempotent missing artifact

- **WHEN** `env remove` и owned artifact уже отсутствует
- **THEN** считается идемпотентным успехом, записывается в audit

#### Scenario: Partial failure → cleanup_failed

- **WHEN** `env remove` частично fails (e.g. worktree remove error)
- **THEN** state `cleanup_failed`, повторный `remove` продолжает с оставшихся artifacts

#### Scenario: Multiple UUIDs resolved independently

- **WHEN** `env remove UUID1 UUID2` runs with UUIDs from different projects
- **THEN** each target's repository and cluster context is resolved separately

#### Scenario: No arguments preserves cwd semantics

- **WHEN** `env remove` runs from inside an exact registered worktree
- **THEN** it resolves exactly that environment

#### Scenario: Planning preflight aborts before mutation

- **WHEN** one target in a multi-target call is unknown
- **THEN** the command aborts with no changes and a sanitized error

### Requirement: Checkout dry-run

`env checkout --dry-run` MUST показывать worktree/config/port/DB plan, Python mode (`reuse|create`) и ownership, dependency inputs и helper argv. Ничего не создаёт; candidate values перепроверяются при execution.

#### Scenario: Dry-run shows plan

- **WHEN** `env checkout feat/x --dry-run`
- **THEN** plan выводится, ничего не создаётся

### Requirement: Checkout examples

Checkout examples MUST document the supported modes:

Общая БД, без её копирования:

```bash
odcli --project . env checkout feat/CMRT-123 \
  --base origin/dev \
  --config ./odoo.conf \
  --db-mode shared
```

Отдельная БД в том же PostgreSQL-кластере:

```bash
odcli env checkout feat/CMRT-123 \
  --base origin/dev \
  --config ./odoo.conf \
  --db-mode copy \
  --source-db comerta \
  --target-db comerta_cmrt_123
```

Основные options:

```text
--base REF                  default: HEAD
--config PATH               default: <repo>/odoo.conf
--name TEXT                 default: <repo-name>:<branch>
--db-mode [shared|copy]     default: shared
--source-db NAME            inferred only when odoo.conf contains exactly one db_name
--target-db NAME            default: safe <source>_<branch>_<short-hash>
--odoo-bin PATH             default: project manifest
--python TEXT               existing venv interpreter; with --create-venv also accepts uv selector
--create-venv               explicit opt-in; create isolated owned venv under environment root
--http-port INTEGER         default: allocated automatically
--json                      machine-readable result for agents
```

Если project manifest отсутствует, `checkout` завершается подсказкой выполнить `odcli init`; runtime discovery не перечитывает VS Code автоматически.

#### Scenario: Missing manifest prompts init

- **WHEN** `env checkout` без `.odcli/project.toml`
- **THEN** error с подсказкой `odcli init`

### Requirement: Checkout backup provenance comparison

Before checkout creates a catalog row, worktree, config, Python environment, backup, database, or process, `EnvironmentResource` SHALL compare the effective base ref against the source branch of the backup audit row mapped to the effective source database through `restores.backup_id`. The lookup SHALL NOT require the archive to be available, readable, or in a restorable state. The typed comparison SHALL be `matched` when normalized ref texts are equal, `mismatched` when both are known and unequal, and `unknown` only when the mapping/backup audit row is absent or its recorded branch is null.

A known mismatch SHALL abort before any mutation in shared and copy modes. Checkout SHALL not automatically reset the administrator.

#### Scenario: Known branch matches

- **WHEN** effective base and mapped backup source branch are both `release/19`
- **THEN** checkout planning reports `matched` and may continue

#### Scenario: Known mismatch aborts

- **WHEN** effective base is `release/19` and mapped backup branch is `develop`
- **THEN** checkout fails before catalog/worktree/database mutation with both non-secret refs reported

#### Scenario: Unavailable archive retains known mismatch

- **WHEN** the mapped backup records `release/18`, the effective base is `release/19`, and the archive was deleted
- **THEN** provenance is `mismatched` and checkout rejects it rather than applying the legacy-unknown exception; freshness separately reports unavailable

### Requirement: Legacy unknown provenance policy

Unknown provenance SHALL be accepted only when the checkout call supplies an explicit source database (`EnvironmentCheckoutOptions.source_database` / `--source-db`). Accepted unknown provenance SHALL emit a stable warning that branch compatibility cannot be verified and identify the explicit source database. A source database inferred from project default or config SHALL not satisfy this exception; checkout SHALL fail with guidance to pass `--source-db` or refresh a provenance-bearing backup.

#### Scenario: Explicit legacy source accepted

- **WHEN** a legacy mapped backup has no branch and checkout explicitly supplies its source database
- **THEN** checkout reports `unknown`, emits the compatibility warning, and may continue

#### Scenario: Inferred legacy source rejected

- **WHEN** the same legacy database is selected only from `default_source_database`
- **THEN** checkout fails before mutation with guidance to opt in explicitly or refresh

### Requirement: Checkout freshness preparation ordering

Checkout SHALL evaluate `refresh_after_hours` only after project/source/base resolution. When configured and the current default is stale, missing an available mapped backup, or missing its file, checkout SHALL invoke the shared project preparation workflow with restore enabled before producing the final checkout plan. The workflow SHALL complete any required download, restore, and default switch before checkout re-resolves the source database, provenance, target name, and remaining plan.

Checkout SHALL not create its environment row or owned artifacts until preparation succeeds. When freshness is not configured, checkout SHALL not refresh based on age. Provenance validation remains mandatory either way.

#### Scenario: Stale default refreshed before plan

- **WHEN** the mapped backup age reaches the configured threshold
- **THEN** preparation creates and selects a fresh local default before checkout calculates its final source/target and creates artifacts

#### Scenario: Preparation failure leaves checkout untouched

- **WHEN** required refresh restores a database but its post-restore step fails
- **THEN** checkout creates no environment/worktree artifacts, the prior default remains selected, and preparation retains its database/mapping for diagnosis

### Requirement: Checkout dry-run reports the same provenance decision

Public additive `EnvironmentResource.plan_checkout()` SHALL return a secret-free `EnvironmentCheckoutPlan` and resolve the same effective base, source database, audit provenance, legacy-unknown rule, and availability-aware freshness state as execution using read-only catalog access. It SHALL report whether preparation would download/restore/switch the default and then report the resulting checkout intent without performing network, catalog migration/write, manifest write, database, filesystem, Git, Python, lock-held mutation, or admin reset. Additive `checkout_with_plan()` SHALL return `EnvironmentCheckoutResult` containing the realized environment and final public plan recalculated after preparation. Canonical `checkout()` SHALL delegate to that method and continue returning only `DevelopmentEnvironment` as required by the main spec. Private `_CheckoutPlan` SHALL remain internal execution state and SHALL NOT be serialized or exposed by either public model.

The public plan SHALL expose only name, branch, effective base, database mode/source/target, create-or-reuse Python mode, typed provenance, typed freshness, prospective preparation actions, and warnings. It SHALL NOT expose parsed config values, passwords, paths, executable/argv data, or prospective identifiers.

Dry-run is an observation, not a reservation; real checkout SHALL recheck everything after acquiring the preparation lock.

#### Scenario: Dry-run known mismatch

- **WHEN** dry-run observes a known source/base mismatch
- **THEN** it returns the same rejection as execution and performs no mutation

#### Scenario: Dry-run stale plan

- **WHEN** dry-run observes a stale current default with a compatible configured test branch
- **THEN** it reports the refresh/restore/default-switch steps and subsequent checkout intent without executing them

#### Scenario: Canonical checkout remains compatible

- **WHEN** an SDK caller uses `EnvironmentResource.checkout()` after this change
- **THEN** it receives `DevelopmentEnvironment`, while `commands/env.py` may use additive `checkout_with_plan()` for the same execution plus a secret-free report

### Requirement: Executable environment commands

Every `EnvironmentResource` operation that can launch a child process SHALL expose a sibling command method, including checkout, Python synchronization, refresh/database preparation, removal, and any process-spawning pgAdmin path found by the migration audit. Existing convenience methods SHALL delegate to those commands.

#### Scenario: Checkout command is inspected and run

- **WHEN** a caller builds `checkout_command(project, branch, options=...)`, inspects it, and calls `.run()`
- **THEN** the exact captured Git, uv, Odoo, PostgreSQL, and action steps shown by the command are consumed in order

### Requirement: Checkout domain-plan compatibility

`plan_checkout()` SHALL remain the public domain projection and `checkout_with_plan()` SHALL remain compatible. Both SHALL derive from the same captured checkout command used by `checkout()`; no method SHALL rebuild executable steps independently.

#### Scenario: Existing checkout plan caller

- **WHEN** an existing caller uses `plan_checkout()` or `checkout_with_plan()`
- **THEN** it receives the documented `EnvironmentCheckoutPlan` fields
- **AND** subsequent execution uses the captured command snapshot corresponding to that plan

### Requirement: Checkout planning and stale safety

Checkout command construction SHALL create no environment root, worktree, venv, config, lock, catalog migration, database, backup, or runtime record. Execution SHALL revalidate the captured base revision, paths, port, database provenance, and identity under the existing operation locks and SHALL fail stale before mutation rather than recompute them.

#### Scenario: Checkout preview remains non-mutating

- **WHEN** checkout command construction or CLI dry-run runs against an empty target
- **THEN** every target artifact remains absent and all required read-only process probes are recorded as observations

#### Scenario: Allocated port becomes occupied

- **WHEN** the captured checkout port becomes unavailable before `.run()`
- **THEN** checkout raises `StalePlanError` before creating catalog or filesystem state
- **AND** it does not allocate a replacement port silently

### Requirement: Environment mutation progress

Normal Rich execution of `env checkout` and `env sync` SHALL observe the existing immutable command plan and report each genuinely long logical step as started, completed, or failed with elapsed time. Non-TTY Rich output SHALL use sparse deterministic lines; TTY Rich MAY update a live status. Dry-run SHALL show only the plan, and JSON/TOON SHALL emit no progress.

#### Scenario: Slow checkout step is visible

- **WHEN** a controlled checkout step remains running after it starts
- **THEN** Rich output identifies that step before it finishes and later reports its completion or failure with elapsed time

#### Scenario: Checkout dry-run

- **WHEN** `env checkout --dry-run` is rendered in any format
- **THEN** it reports planned steps but no step is presented as started or completed

#### Scenario: Environment machine output

- **WHEN** checkout or sync runs in JSON or TOON mode
- **THEN** stdout is exactly one final envelope without progress or ANSI content

### Requirement: Owned Python runtime readiness preflight

Checkout with `create_venv=true` SHALL execute exactly one bounded, immutable and inspectable preflight using the selected owned Python, the recorded Odoo entry point, the resolved worktree cwd and child environment before recording the environment as `ready`. The preflight SHALL invoke `<python> <odoo-bin> --help` through the existing process boundary with a 30-second timeout, SHALL require exit code zero, and SHALL redact its command and diagnostics through the existing projection. Shared Python environments SHALL retain their existing checkout behavior.

#### Scenario: Valid owned environment becomes ready

- **WHEN** the owned Python executes the configured Odoo entry point preflight successfully
- **THEN** checkout completes its remaining postconditions and SHALL be eligible to record `ready`

#### Scenario: Missing Odoo runtime dependency

- **WHEN** the owned Python cannot import the configured Odoo entry point and the preflight exits non-zero
- **THEN** checkout never reports `ready`, returns an actionable sanitized failure, and runs the existing rollback path

#### Scenario: Preflight timeout

- **WHEN** the owned-runtime preflight does not finish within 30 seconds
- **THEN** checkout terminates that exact preflight through the existing process boundary and leaves failed cleanup retryable

### Requirement: Direct and retryable COPY environment removal

Removal of an SDK-owned COPY environment SHALL drop its exact target database through the existing guarded direct PostgreSQL drop operation after revalidating authoritative cluster, database, environment and filestore ownership. It SHALL NOT require, start, or contact an Odoo HTTP listener, and SHALL NOT require the recorded HTTP port to be free. The same `env remove` operation SHALL accept `cleanup_failed` as a retryable source state and continue only the retained owned cleanup steps. Ownership mismatch, an active owned Odoo runtime, unsafe database sessions, unreadable destructive evidence, or a changed target SHALL fail closed without terminating a process or session implicitly.

#### Scenario: Stopped COPY environment is removed

- **WHEN** a stopped COPY environment has matching authoritative ownership and no unsafe active database use
- **THEN** `env remove` directly drops the exact target database, verifies its absence, cleans the remaining owned artifacts, and records `removed`

#### Scenario: Cleanup retry resumes

- **WHEN** an earlier removal left the environment in `cleanup_failed` with retained owned artifacts
- **THEN** a repeated `env remove` revalidates current evidence, skips already absent artifacts, and safely continues cleanup

#### Scenario: Unrelated port occupant

- **WHEN** an unrelated process occupies the environment's recorded HTTP port but no owned runtime or unsafe database use is proven
- **THEN** removal neither terminates nor contacts that process and the unrelated port alone does not block direct database cleanup

#### Scenario: Unsafe active use fails closed

- **WHEN** the exact environment owns a live Odoo runtime or current database sessions cannot be safely attributed and cleared by an existing explicit contract
- **THEN** removal performs no database drop and reports the blocking evidence

### Requirement: Jira ticket branch allocation for CLI checkout

The CLI checkout adapter SHALL validate its positional Jira ticket against `^[A-Z][A-Z0-9]+-[1-9][0-9]*$` before constructing Git or catalogue probes. Under the existing repository checkout lock it SHALL resolve branch evidence for that repository from exactly: all local branch refs, all environment catalogue rows including removed/history rows, and current `origin` branch heads obtained by recorded `git ls-remote --heads` steps without fetch or ref mutation. Only the exact ticket and `<TICKET>_<N>` with a positive decimal `N` SHALL match; the unsuffixed branch is iteration zero. The resolved branch SHALL be the unsuffixed ticket when no match exists, otherwise `<TICKET>_<max(N)+1>` across the union; gaps and removed names SHALL never be reused.

The captured resolution and per-source evidence SHALL feed the existing exact-branch `EnvironmentResource.checkout_command` and all derived worktree, catalogue, environment-name, database/provenance, output and cleanup values. Execution SHALL revalidate that the captured new name remains absent from all three sources before mutation and SHALL raise a stale/conflict error instead of choosing another suffix. The public SDK exact-branch input and `EnvironmentCheckoutOptions.name` SHALL remain compatible; only the CLI adapter imposes Jira allocation.

#### Scenario: First ticket checkout

- **WHEN** no local ref, catalogue history row, or current `origin` head matches `PROJ-123`
- **THEN** CLI checkout captures branch `PROJ-123` created from the selected base

#### Scenario: Sparse history reserves iterations

- **WHEN** matching evidence contains `PROJ-123`, `PROJ-123_1`, and a removed catalogue row for `PROJ-123_3`
- **THEN** CLI checkout captures `PROJ-123_4` and does not fill the missing iteration

#### Scenario: Invalid ticket fails before probes

- **WHEN** the positional value does not match the Jira ticket grammar
- **THEN** checkout fails as usage before Git, catalogue, worktree, filesystem, database or process planning

#### Scenario: Late branch collision

- **WHEN** a local ref, catalogue row, or current `origin` head acquires the captured branch after planning
- **THEN** execution fails stale before mutation and a later invocation performs a fresh allocation

#### Scenario: Candidate source is unavailable

- **WHEN** local-ref, catalogue-history, or `origin`-head inspection cannot complete successfully
- **THEN** checkout fails before mutation and SHALL NOT treat the unavailable source as an empty candidate set

### Requirement: Applied environment settings evidence

The catalogue SHALL store one versioned, secret-free `applied_settings_json` snapshot for each environment through the next sequential additive schema migration. Successful checkout SHALL atomically record normalized field-specific evidence for: effective Python selector/path/ownership, dependency input identities and redacted semantic fingerprints, SDK-managed Odoo config values, normalized add-on paths, and Git ticket/resolved-branch/base provenance. Successful `env sync` SHALL update only the Python/dependency evidence it actually applies and preserve the other components. A failed, interrupted or dry-run checkout/sync SHALL NOT advance any applied evidence; legacy rows without sufficient evidence SHALL remain valid and diagnose the affected component as `unknown`.

Whole-file hashes SHALL NOT define drift. Odoo settings SHALL be parsed and normalized by field, lists/paths SHALL use the existing canonicalization, and dependency fingerprints SHALL use the existing secret projection before digesting normalized semantic requirement entries so no secret enters stored evidence or a fingerprint. Allocated port and selected database SHALL compare to their recorded environment bindings rather than later project defaults. Ordinary worktree changes, ahead/behind counts and uncommitted files SHALL be reported only as Git context, not configuration drift.

#### Scenario: Checkout records applied components

- **WHEN** checkout reaches all readiness postconditions
- **THEN** its environment row atomically records complete applied evidence corresponding to the artifacts and bindings made ready

#### Scenario: Sync updates only applied dependencies

- **WHEN** `env sync` successfully applies a changed dependency input
- **THEN** Python/dependency evidence advances while Odoo config, add-on and Git applied evidence remains unchanged

#### Scenario: Failed sync preserves evidence

- **WHEN** compile or install fails during `env sync`
- **THEN** the prior applied snapshot remains authoritative and diagnostics continue to report the unapplied input as drift

#### Scenario: Legacy evidence remains unknown

- **WHEN** a pre-migration environment has no trustworthy evidence for a component
- **THEN** the component reports `unknown` with a concrete reason and no inferred applied value is backfilled

#### Scenario: Current input cannot be inspected

- **WHEN** a current project setting or managed artifact cannot be parsed or read safely
- **THEN** diagnosis reports the affected component as `unknown`, preserves applied evidence, and exposes no raw secret or unsafe path

### Requirement: Tracker-neutral ticket allocation
Environment checkout planning SHALL model ticket allocation with neutral `Ticket` names while preserving first unsuffixed branch, subsequent `_N`, maximum evidence across local/catalogue/origin, non-reuse of historical iterations, and stale-collision failure. [Source: GH#64 §11]

#### Scenario: Allocate without tracker integration
- **WHEN** a valid ticket key is supplied
- **THEN** allocation uses only Git refs and catalogue evidence and never verifies the ticket through an external API

### Requirement: Unified environment storage location
New global environment worktrees SHALL live below the unified `~/.odcli/` root while repository-local `<project>/.odcli/` remains unchanged. [Source: GH#64 §7]

#### Scenario: Create environment after migration
- **WHEN** checkout provisions a new environment
- **THEN** every SDK-owned global artifact is rooted below the canonical user root

### Requirement: CheckoutInventory with main checkout row

`EnvironmentResource` SHALL expose a `CheckoutInventory` projection that includes the main checkout of each selected project as the first typed row with `kind = main | environment`, a stable `project_id`, and a nullable `environment_id`. The main checkout SHALL NOT be modelled as a synthetic environment. The base row SHALL contain working identity and state: kind/name and project; branch, short SHA, and canonical worktree path; commits ahead of the base branch plus added and deleted lines; compact Odoo status `running | stopped | unavailable` without PID or metrics; and bound database/DB mode when applicable.

`CheckoutInventory` SHALL be built from one canonical `EnvironmentMonitor.snapshot()` per sample plus Git facts of the main checkout. A separate monitor or collector SHALL NOT be added. The raw `EnvironmentMonitor.snapshot()` SHALL remain the canonical source for `odcli ps`, the Python SDK, FastAPI, and the dashboard.

#### Scenario: Main checkout is the first row

- **WHEN** `CheckoutInventory` is built for a project with one environment
- **THEN** the first row has `kind=main` and no synthetic environment is created

#### Scenario: Stopped checkout stays visible

- **WHEN** the main checkout's Odoo is stopped
- **THEN** the main checkout row remains visible with status `stopped` and no PID
