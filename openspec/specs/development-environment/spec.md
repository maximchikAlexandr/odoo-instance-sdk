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
) -> list[DevelopmentEnvironment]: ...

def remove(self, selector: EnvironmentSelector) -> None: ...
```

Selector — UUID or exact name (`str | DevelopmentEnvironment`); ambiguity is an error. `history()` и `list(verify=)` не входят в public API. Events остаются в catalog; `doctor` читает их internally.

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

`EnvironmentResource.list()` остаётся источником environment rows для SDK callers. `EnvironmentMonitor` reads `BackupCatalog.list_environments` / `list_environment_runtimes` directly (via `get_catalog_path()` or injected `catalog_path`) and MUST NOT reimplement catalog schema or scan the filesystem. `odcli env list` / `odcli monitor` consume `EnvironmentMonitor.snapshot()`. `EnvironmentResource` does not grow runtime methods; `environment_runtime` is catalog-internal.

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

### Requirement: Catalog current-runtime record (schema v8 → v9)

Catalog MUST хранить одну current runtime-запись на environment в таблице `environment_runtime` (schema migration v8 → v9, `CURRENT_SCHEMA_VERSION = 9`).

`BackupCatalog` MUST предоставлять read-only `get_environment_runtime(environment_id)` и `list_environment_runtimes()`, и write `upsert_environment_runtime(...)` / `clear_environment_runtime(environment_id)` (только из `run_foreground`).

Collector (`EnvironmentMonitor`) reads runtime rows read-only. PID safety: collector считает process живым только при `psutil.Process(pid).create_time() == recorded_create_time` и `psutil.pid_exists(pid)`; mismatch → `runtime.state="stopped"`.

#### Scenario: Migration adds runtime table

- **WHEN** catalog at schema v8 is opened
- **THEN** `environment_runtime` table is created, `PRAGMA user_version = 9`, existing environments have no runtime row

#### Scenario: Upsert is one-row-per-environment

- **WHEN** `upsert_environment_runtime(env_id, ...)` is called twice for the same environment
- **THEN** one row exists with the latest values (no duplicates)

#### Scenario: Collector reads runtime read-only

- **WHEN** `EnvironmentMonitor.snapshot()` runs
- **THEN** it calls `list_environment_runtimes()` (read-only); collector never calls `upsert`/`clear`

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

`shared` mode:

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

В обоих modes Odoo Core и project requirements компилируются одним `uv pip compile` в environment-owned `requirements.lock`.

- reused venv: `uv pip install --python <project-python> -r <lock>` сохраняет unrelated project tools;
- owned venv: `uv pip sync --python <environment-python> <lock>` обеспечивает isolation;
- uv writes сериализуются `flock` по canonical Python-environment path;
- repo-local dependency files rebase в worktree; lock/fingerprint относятся к worktree;
- `env sync --upgrade` обновляет pins, обычный sync сохраняет их; failed compile не заменяет valid lock;
- `run`/`shell` MUST NOT вызывать `sync_python`. Write path для lock/venv — только `env sync`. Drift показывает `doctor` / `deps verify`.

Runtime prefix всегда `[recorded-python, odoo-bin]`. `uv venv`/`pip compile`/`pip sync` и fingerprint — internal implementation `sync_python()`, не public venv module.

#### Scenario: Failed compile keeps valid lock

- **WHEN** `uv pip compile` fails, valid `requirements.lock` already exists
- **THEN** valid lock не заменяется, sync continues with existing lock

### Requirement: `env sync`

`env sync [ENVIRONMENT] [--upgrade]`:

- regular sync preserves pins; `--upgrade` updates pins;
- repo-local dependency files rebase в worktree;
- uv writes сериализуются `flock`.

#### Scenario: Sync upgrade

- **WHEN** `env sync --upgrade`
- **THEN** pins обновляются в `requirements.lock`

### Requirement: `env list`

```bash
odcli env list
odcli env list --all
odcli env list --json
```

Default table:

```text
ID  NAME  STATE  OBSERVED  BRANCH  PYTHON_MODE  DB_MODE  DATABASE  PORT  LAST_USED  WORKTREE
```

По умолчанию скрываются только `removed`; `failed` и `cleanup_failed` видны.

Quick reconciliation проверяет: наличие worktree, generated config, recorded Python/ownership/lock, port state, owned backup. `OBSERVED` — `port-free|port-occupied|unknown`, не process ownership.

Reconciliation ALWAYS выполняется в `env list`. Расширенные filesystem checks принадлежат `doctor`, не флагу `list(verify=)`.

`OBSERVED = unknown` — когда port state не может быть определён (e.g. environment в `creating`/`failed`/`removed` state, или `http_interface`/`http_port` unreadable).

`DATABASE` column: для `shared` mode показывает `source_db_name`; для `copy` mode показывает `target_db_name`.

`--all-projects` (CLI-level flag, см. `cli-odcli` spec) читает durable global registry из любой directory и не требует project context.

`--all` — include `removed` environments (по умолчанию скрыты).

#### Scenario: Default hides removed

- **WHEN** `env list` без `--all`
- **THEN** `removed` environments скрыты, `failed`/`cleanup_failed` видны

#### Scenario: Reconciliation detects missing worktree

- **WHEN** `env list` (с reconciliation) для environment где worktree отсутствует в `git worktree list --porcelain -z`
- **THEN** environment listed с indicator missing worktree

#### Scenario: OBSERVED reflects live socket.bind

- **WHEN** `env list` для environment с allocated port и `socket.bind((http_interface, http_port))` succeeds
- **THEN** `OBSERVED` = `port-free`; если fails → `port-occupied`

#### Scenario: Reconciliation detects missing generated config

- **WHEN** `env list` для environment где generated `odoo.conf` missing
- **THEN** environment listed с indicator missing config

#### Scenario: Reconciliation detects missing owned backup

- **WHEN** `env list` для copy environment где owned backup file missing
- **THEN** environment listed с indicator missing backup

#### Scenario: Reconciliation detects missing Python or lock

- **WHEN** `env list` для environment где recorded Python path не существует OR `requirements.lock` missing
- **THEN** environment listed с indicator missing Python/lock

### Requirement: `env remove`

```bash
odcli env remove <environment-id> --dry-run
odcli env remove <environment-id> --yes
```

Перед изменениями показать план и выполнить полный preflight. Без `--yes` требуется Click confirmation.

Default cleanup matrix:

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

Bulk prune, автоматическое удаление по возрасту и `--force` для грязных worktrees не входят в scope.

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

### Requirement: Checkout dry-run

`env checkout --dry-run` MUST показывать worktree/config/port/DB plan, Python mode (`reuse|create`) и ownership, dependency inputs и helper argv. Ничего не создаёт; candidate values перепроверяются при execution.

#### Scenario: Dry-run shows plan

- **WHEN** `env checkout feat/x --dry-run`
- **THEN** plan выводится, ничего не создаётся

### Requirement: Checkout examples

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
