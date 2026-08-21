## Context

SDK сегодня: `OdooClient` (facade: `instance`, `backups`), `InstanceFactory`/`OdooInstance` (process lifecycle, DB ops), `BackupResource`/`DatabaseResource`, `BackupCatalog` (SQLite v2, в `user_cache_dir`/`backups.sqlite3`), `StartConfig`/`InstanceConfig`/`OdooClientConfig`, internal helpers (`odoo_config`, `server`, `health`, `paths`, `urls`).

Нет: CLI/project manifest, worktree/generated-config primitives, Python-environment binding/optional provisioning, durable environment audit, runtime/shell coordinators и cleanup.

Текущие швы, которые этот issue обязан пересмотреть, а не обойти:

- `OdooInstance.run()` берёт executable из `OdooClientConfig`; один client не может честно нести несколько окружений с разным Python/`odoo-bin`.
- `from_config()` требует master password до любого runtime call, хотя `run`/`shell` ему не нужны.
- `StartConfig.from_odoo_config(path)` не фиксирует `config_path` в фактический `path`, а `_build_cli_args()` может добавить второй временный config из-за `db_password`.
- Catalog лежит в cache (`user_cache_dir` / `backups.sqlite3`) и называется backup-only, хотя уже хранит restore/drop audit.

`restore()` также не отправляет target `name` в POST.

В корне самого SDK нет `odoo.conf`; CLI должен работать в Odoo-репозитории-потребителе и принимать `--config`, используя `<repo>/odoo.conf` только как default discovery.

## Goals / Non-Goals

**Goals:**

- Короткий core loop: `init` → `checkout` → `run`/`shell` → `remove`, без ручного управления worktree/config/venv/БД.
- `odcli` — тонкий adapter: резолвит context, печатает human/JSON, вызывает SDK. Runtime lifecycle не переезжает в CLI.
- `DevelopmentEnvironment` владеет артефактами/ownership/cleanup; `OdooInstance` владеет процессом Odoo. Жёсткий шов.
- Один durable SQLite на backups metadata + environments + events. Второй catalog запрещён.
- Агент-ориентированный: stable `--json`, deterministic errors, `--no-input`, dry-run.
- Internal: Git, `uv`, `fcntl.flock`, generated config — за `EnvironmentResource`, не public.

**Non-Goals:**

- Background daemon, persisted process registry, `running` provisioning state.
- XML-RPC/JSON-RPC fallback для `eval`/`exec`/`module`/`translations`.
- Public `GitWorktree`/`PythonVenv`/`LockManager`/`ModuleResource`/`TranslationResource`/`client.catalog`/`client.doctor`.
- Module install/uninstall, `doctor --fix`, bulk prune.
- Killing external processes, deleting branches, remote restore/drop.
- VS Code tasks/hooks/attach import; installing `uv`/OS packages.
- Windows locking; MVP uses Unix `fcntl`.
- Compliance-grade audit.
- Silent environment defaults (last-used, single-ready).

## Product Decisions

Эти решения фиксируют границы продукта. Реализация не должна их ослаблять ради «удобного» следующего среза.

1. **`DevelopmentEnvironment` и `OdooInstance` — разные модули.** Environment владеет артефактами, ownership и cleanup. Instance владеет процессом Odoo. Ни Git/worktree/remove на instance, ни `run`/`shell`/`start` на environment.
2. **Resolved runtime identity живёт на `InstanceConfig`**: `command_prefix` и `default_cwd`. `OdooClientConfig.executable` — только fallback для ручного `instance(base_url=...)`.
3. **Создание instance не требует master password.** `from_config()` и `from_environment()` могут вернуть instance с `master_password=None`. `MasterPasswordRequiredError` возникает только на mutating DB-методах.
4. **Один durable SQLite** на все ownership/audit данные. Второй catalog для environments запрещён. ZIP могут остаться в cache; метаданные, environments и events — только в durable файле. Catalog internal; снаружи `client.backups` и `client.environments`.
5. **CLI не является третьим runtime**: нет process table, log capture, port daemon и собственного Git/uv API.
6. **Первый CLI — `init`, `env checkout|sync|list|remove`, `run`, `shell`, `doctor`.** Остальные команды — post-MVP application coordinators без `ModuleResource`/`TranslationResource`/`PythonResource`.
7. **Context: внутри registered worktree всё выводится из cwd.** Иначе только explicit `--project` и `--env`/positional selector. Ни «последний использованный», ни молчаливый выбор единственного `ready`.
8. **Git, `uv`, `fcntl.flock` и generated `odoo.conf` спрятаны за `EnvironmentResource`.** Публичных `GitWorktree`, `PythonVenv`, `LockManager` нет.
9. **Один internal foreground subprocess primitive.** Снаружи три явных operations: `run_foreground()`, `shell()`, `run_shell_script()`. Существующий `run()` не перегружается.
10. **`StartConfig.from_odoo_config(path)` записывает фактический `path`;** runtime передаёт ровно один `--config`. Второй временный config из-за `db_password` запрещён, если persistent generated conf уже `0600`.
11. **`OdooClient` остаётся фасадом**: `instance`, `backups`, `environments`. Process registry остаётся на client. Catalog, locks, Git, `uv` и `doctor` не становятся методами client.

## Decisions

### D1: Responsibility boundary — `DevelopmentEnvironment` vs `OdooInstance`

```
┌──────────────────────────────────────────────────────────────────────┐
│  Abstraction            │ Responsibility                              │
├─────────────────────────┼─────────────────────────────────────────────┤
│  OdooClient             │ Facade: instance, backups, environments.    │
│                         │ Держит существующий process registry.       │
│                         │ Не Git, не locks, не uv, не doctor.         │
│  ProjectConfig          │ Public immutable declarative project        │
│                         │ discovery/defaults from .odcli/project.toml │
│  DevelopmentEnvironment │ Provisioning record: worktree/config,      │
│                         │ reused-or-owned Python binding, port,      │
│                         │ DB ownership and cleanup audit              │
│  EnvironmentResource    │ checkout(), sync_python(), get(), list(),  │
│                         │ history(), remove(). Git/uv/flock/generated │
│                         │ config остаются internally                  │
│  InstanceFactory        │ Materialize OdooInstance from config or     │
│                         │ registered development environment          │
│  OdooInstance           │ Runtime lifecycle: foreground run/shell,   │
│                         │ captured run/run_shell_script, background   │
│                         │ start, stop, status, readiness, DB ops      │
└─────────────────────────┴─────────────────────────────────────────────┘
```

`DevelopmentEnvironment` и `OdooInstance` описывают разные вещи и не заменяют друг друга. Это жёсткий шов, не рекомендация.

CLI context inference не протекает в Python SDK: SDK-вызовы всегда получают explicit `ProjectConfig`/project path и explicit environment selector.

### D2: `OdooClient` facade — `instance`/`backups`/`environments`

```
OdooClient
├── instance          # InstanceFactory
├── backups           # BackupResource
└── environments      # EnvironmentResource
```

Process registry (`register_process`/`get_process`) остаётся на client. Catalog открывается internally и не экспортируется как `client.catalog`.

### D3: Runtime executable на instance, не только в global client config

Revision текущего `OdooInstance.run()`, который читает `self._client.config.executable`:

```python
class InstanceConfig:
    # existing fields...
    command_prefix: tuple[str, ...] | None = None
    default_cwd: Path | None = None
```

Правила prefix/cwd:

- `from_environment()` записывает recorded `[python, odoo-bin]` и resolved `runtime_cwd`;
- `from_config(path)` записывает `[OdooClientConfig.executable]` если executable set, иначе `None` (fallback на `OdooClientConfig.executable`); не требует master password;
- ручной `instance(base_url=...)` оставляет `command_prefix=None` и падает назад на `OdooClientConfig.executable`;
- `run()`, `start()`, `run_foreground()`, `shell()`, `run_shell_script()` используют instance prefix, затем client fallback.

### D4: Master password — не инвариант конструкции instance

- `from_config()` не поднимает `MasterPasswordRequiredError`, если `admin_passwd` отсутствует: поле остаётся `None`;
- `from_environment()` всегда оставляет `master_password=None` и не читает его из generated conf для runtime;
- copy checkout / `backup` / `restore` / `drop` требуют пароль в момент mutating DB call и поднимают `MasterPasswordRequiredError` там.

### D5: `InstanceFactory.from_environment()` MUST

- принимать только `ready` environment;
- читать generated `odoo.conf` через существующий config flow;
- применять recorded Python interpreter (shared or owned), Odoo entry point и worktree как defaults для запуска;
- использовать recorded resolved runtime paths, не перечитывая project manifest;
- не требовать master password;
- возвращать обычный `OdooInstance`, а не новую runtime wrapper abstraction;
- не переносить Git, cleanup или audit methods в `OdooInstance`.

### D6: Required current-API revisions

Чтобы generated config был единственным runtime config:

- `StartConfig.from_odoo_config(path)` MUST устанавливать `config_path` в фактический `path`, а не ждать option `config_path` внутри файла;
- `_build_cli_args()` MUST передавать ровно один `--config`;
- при persistent generated config с правами `0600` нельзя добавлять второй временный config только из-за `db_password`.

`DatabaseResource.restore()` MUST отправлять `"name": target_database_name` в POST body.

### D7: Context-aware command resolution — два правила

```
┌──────────────────────────────────────────────────────────────────────┐
│  Rule 1: cwd inside exact registered worktree                       │
│          → project + environment inferred from that record           │
│                                                                      │
│  Rule 2: otherwise → explicit flags required                         │
│          --project PATH for project commands                         │
│          --env SELECTOR or positional ENVIRONMENT for instance cmds  │
│                                                                      │
│  Nearest .odcli/project.toml upward to Git/fs boundary =             │
│  explicit project discovery, NOT environment guessing                │
└──────────────────────────────────────────────────────────────────────┘
```

Запрещено: выбирать «последний использованный» environment; молча брать единственный `ready` environment проекта, если cwd не является его worktree; резолвить global default project.

Project resolution:

1. Explicit global `--project PATH` (любой путь внутри project).
2. Ближайший `.odcli/project.toml` от current directory вверх до Git/filesystem boundary.
3. Exact registered worktree containing current directory, resolved через canonical Git common dir.
4. Иначе — ошибка с подсказкой `odcli init` или `--project`.

Environment resolution для instance commands (`run`, `shell`; post-MVP также `eval`, `exec`, `module`, `translations`, `deps verify`):

1. Explicit root `--env SELECTOR` — UUID либо однозначное имя; option допустим только для instance commands.
2. Exact registered worktree containing current directory.
3. Иначе — ошибка: либо `cd` в worktree, либо `--env`, со списком candidates если их несколько. Никогда не выбирать единственный `ready` молча и никогда не выбирать по recency.

Command-specific rules:

- `env checkout BRANCH`, default `env list` и `doctor` требуют project context;
- `env list --all-projects` читает durable global registry из любой directory и не требует project; default list ограничен current project, а `--all` означает include removed;
- lifecycle `env sync/remove [ENVIRONMENT]` используют positional selector; без него команда разрешена только из exact registered worktree. Root `--env` с lifecycle command — usage error;
- root context options `--project`/`--env` должны появляться в resolved plan/JSON provenance как `explicit` или `cwd`; поле `defaulted` для environment не используется.

### D8: Project initialization — manifest location and content

`odcli init` создаёт один declarative manifest:

```text
<repository-root>/.odcli/project.toml
```

Manifest содержит discovery/defaults: Odoo source/bin, source config, project Python interpreter (либо uv selector для explicit creation), dependency files, safe run args и `runtime_cwd`. Secrets/runtime artifacts в repository не пишутся.

Runtime artifacts остаются в platformdirs user directories и связываются с project через canonical Git common dir, а не через имя папки.

### D9: Input modes — interactive / headless / VS Code import

```bash
# Human: prompts only for unresolved required values
odcli init

# Agent/headless: never prompts or hangs
odcli init --no-input \
  --odoo-bin /path/to/odoo-bin \
  --python /path/to/project-venv/bin/python \
  --config /path/to/odoo.conf \
  --requirements /path/to/odoo/requirements.txt \
  --requirements ./requirements.txt
```

Rules:

- in a TTY, missing required values start Click prompts; fully specified values do not;
- `--no-input` forbids prompts and fails with a stable list of missing/ambiguous options;
- an existing non-identical manifest is never overwritten silently; identical init is idempotent;
- `--dry-run --json` returns the resolved manifest plus provenance (`option`, `vscode`, `discovery`, `default`) without writing.

### D10: VS Code import

```bash
odcli init --from-vscode .vscode/launch.json
odcli init --from-vscode .vscode/launch.json --launch-name "Odoo comerta"
```

Importer MUST:

1. Parse VS Code JSON with comments and trailing commas (json5).
2. Consider only `request=launch` Python/debugpy configurations with an Odoo-like `program`; it must not select an unrelated first configuration.
3. In interactive mode show matching profiles; in `--no-input` require `--launch-name` when more than one candidate remains.
4. Import `python`, `program`, `cwd` and structured Odoo `args`: config, database, port, dev/addons/upgrade paths.
5. Support only static `${workspaceFolder}`; named-workspace, env, command, input and unresolved variables are explicit errors.
6. Report and drop operational `-u/-i/--stop-after-init`; never persist them as run defaults.
7. Record source file/profile as non-secret provenance, then use the generated project manifest at runtime; later edits to `launch.json` do not silently change an environment.

`preLaunchTask` and `.vscode/tasks.json` are reported as ignored. Import never executes editor tasks or arbitrary shell commands.

Imported runtime contract:

- `cwd` становится `ProjectConfig.runtime_cwd`; repo-local path хранится относительно manifest и при checkout rebases в worktree, external absolute path остаётся неизменным с portability warning;
- `envFile` и inline `env` для MVP лишь report'ятся как ignored; values не читаются, не копируются и не печатаются.

Launch mapping:

| VS Code field/arg | Project/environment destination |
|---|---|
| `python` | `ProjectConfig.python`; default checkout reuses this existing venv interpreter |
| `program` | `ProjectConfig.odoo_bin` |
| `-c`/`--config` | `source_config` |
| `-d`/`--database` | `default_source_database` |
| `--http-port` | `preferred_http_port` seed; checkout повторно проверяет free/unique и может выбрать следующий |
| `--addons-path`/`--upgrade-path` | config overlays; repo-local entries rebase в worktree, external remain absolute with warning |
| `--dev` | safe `default_run_args`; shell/automation не наследуют его неявно |
| `cwd` | `runtime_cwd` по правилам выше |

`-u/--update`, `-i/--init`, `--stop-after-init` всегда drop'ятся с warning и не persist'ятся.

Fixture `/odoo/comerta/.vscode/launch.json` обязан выбрать `Odoo comerta`, а не первый Node profile; импортировать external Python/Odoo/config paths, `CMRT-361_1`, port seed `8068` и `--dev=qweb,xml`; `-u comerta_base` только report/drop по правилу выше.

При checkout resolved `runtime_cwd` snapshot'ится в environment; runtime не зависит от дальнейших изменений manifest/launch file.

### D11: Checkout preflight

До любых изменений команда должна:

1. Найти repository root и git common dir через Git CLI.
2. Проверить `git`, `uv`, ref/branch, config/Odoo paths и Python mode. Default требует существующий venv interpreter; без него checkout ошибается с подсказкой `--create-venv`.
3. Проверить, что active environment для этой пары repository + branch ещё не существует.
4. Разрешить source DB и target DB до создания артефактов.
5. Для `copy` проверить локальность source instance, master password и отсутствие target DB.

Dirty основной checkout не блокирует создание worktree и не изменяется командой.

### D12: Worktree placement

Worktree хранится в пользовательском data directory, а не рядом с исходным репозиторием:

```text
<platformdirs.user_data_dir("odoo-instance-sdk")>/environments/
└── <repo-key>/
    └── <environment-id>/
        ├── worktree/
        ├── venv/              # only with --create-venv
        ├── requirements.lock
        └── odoo.conf
```

`repo-key` должен включать безопасный slug и короткий hash от canonical git common dir, чтобы одинаковые имена репозиториев не конфликтовали.

Правила branch:

- существующая локальная branch подключается через `git worktree add`;
- единственная подходящая remote branch создаёт tracking branch;
- отсутствующая branch создаётся от `--base`;
- branch, уже checkout-нутая в другом worktree, вызывает понятную ошибку;
- никакого `--force`, `-B`, reset или удаления существующего worktree;
- состояние читается через стабильный `git worktree list --porcelain -z`.

Использовать системный Git через `subprocess.run([...], shell=False)`. GitPython, Dulwich и pygit2 для этого scope не нужны. Git CLI, porcelain parsing и worktree paths не экспортируются как public module: это internal adapter `EnvironmentResource`.

### D13: Python environment — reuse or create

Default checkout переиспользует interpreter из project manifest/`--python`. Он обязан существовать и сообщать virtual-env prefix; его location может быть external, он регистрируется как `owned=false` и никогда не удаляется SDK.

Только `--create-venv` выполняет `uv venv <environment-root>/venv --python <selector>` и регистрирует artifact как `owned=true`. В обоих modes Odoo Core и project requirements компилируются одним `uv pip compile` в environment-owned `requirements.lock`.

- reused venv: `uv pip install --python <project-python> -r <lock>` сохраняет unrelated project tools;
- owned venv: `uv pip sync --python <environment-python> <lock>` обеспечивает isolation;
- uv writes сериализуются `flock` по canonical Python-environment path;
- repo-local dependency files rebase в worktree; lock/fingerprint относятся к worktree;
- `env sync --upgrade` обновляет pins, обычный sync сохраняет их; failed compile не заменяет valid lock;
- run/shell автоматически sync'ят только при changed inputs; external drift в MVP показывает `doctor`, post-MVP — ещё и `deps verify`;
- reuse осознанно означает shared dependency mutations между worktrees; isolation требует explicit `--create-venv`;
- runtime prefix всегда `[recorded-python, odoo-bin]`; отдельный Python resource не добавляется. `uv venv`/`pip compile`/`pip sync` и fingerprint — internal implementation `sync_python()`, не public venv module.

`create_venv` default всегда `false` и не может прийти из project manifest, VS Code profile или cwd inference: только explicit `--create-venv` текущего checkout (либо explicit SDK option) разрешает создание.

### D14: Generated `odoo.conf`

Исходный config никогда не изменяется. Производный config записывается атомарно с правами `0600` и сохраняет все неизвестные options.

Обязательные изменения:

- элементы `addons_path` и `upgrade_path`, находящиеся внутри исходного repository root, rebased на worktree;
- внешние пути к Odoo core/addons остаются без изменений;
- `http_interface` по умолчанию становится `127.0.0.1`;
- `http_port` берётся из environment registry;
- `db_name` становится source DB в `shared` mode и target DB в `copy` mode;
- `dbfilter` ограничивается выбранной БД;
- DB connection settings, `admin_passwd` и `data_dir` сохраняются, чтобы обе конфигурации работали с тем же PostgreSQL-кластером и filestore root;
- исходные logfile/stdout semantics сохраняются: CLI не добавляет собственный log capture или tee.

Для MVP достаточно stdlib `configparser`, `pathlib`, `shutil`, `tempfile` и `os.replace`. Комментарии generated copy могут не сохраняться; неизвестные keys и values сохраняться обязаны.

### D15: Database modes — shared / copy

Для любого `copy` source/target DB name должен быть безопасным Odoo filestore component: UTF-8 length ≤63 bytes, regex `[A-Za-z0-9_][A-Za-z0-9_.-]*`, но не `.`/`..`; slash, backslash, NUL и absolute/path syntax запрещены. До DB/filesystem mutation canonicalize exact `<data_dir>/filestore/<db-name>`, доказать containment под resolved filestore root и отсутствие escaping symlinks. Эти проверки дополняют PostgreSQL identifier quoting и применяются к backup и future template strategies.

#### `shared`

- backup и restore не выполняются;
- generated config указывает на исходную БД;
- environment не владеет этой БД;
- `remove` не имеет права вызывать `drop()` для исходной БД;
- результат явно предупреждает, что код/process изолированы, а БД и filestore — нет.

#### `copy`

1. Создать отдельный ZIP backup source DB с filestore через существующий `backup()`.
2. Сохранить `backup_id` как принадлежащий этому environment.
3. Восстановить target DB в том же cluster через `restore(..., copy=True, neutralize_database=True)`.
4. Проверить postcondition `exists(target_db) is True`.
5. Только после этого переключить environment в `ready`.

Source Odoo HTTP endpoint должен быть локальным и доступным. Автоматический запуск отдельного source Odoo для backup/restore в этот scope не входит: при недоступном endpoint checkout завершается понятной ошибкой и оставляет аудируемое `failed` environment, которое можно удалить повторяемой командой `remove`.

Target DB никогда не перезаписывается и не удаляется для повторной попытки автоматически.

### D16: Environment registry and audit

Не добавлять ORM, отдельный сервис и второй SQLite. Расширить существующий catalog до schema v3 и переиспользовать его WAL/concurrency/error-handling подход. `BackupCatalog` можно переименовать internally (`SdkCatalog`/equivalent), если имя начнёт врать; public API остаётся `client.backups` и `client.environments`. `EnvironmentCatalog` как отдельный файл или класс-store запрещён.

Catalog/ownership/audit являются durable user data, не cache:

```text
data_root    = Path(platformdirs.user_data_dir("odoo-instance-sdk"))
catalog      = data_root / "catalog.sqlite3"
environments = data_root / "environments"
state_root   = Path(platformdirs.user_state_dir("odoo-instance-sdk"))
locks        = state_root / "locks"
```

Existing backup ZIP payloads могут оставаться в `user_cache_dir("odoo-instance-sdk")`, потому что их отсутствие reconciliation умеет фиксировать как missing. Но backup metadata, environment ownership и append-only history живут только в durable catalog.

Перед v2→v3 schema migration выполнить one-time path migration из legacy `Path(user_cache_dir("odoo-instance-sdk")) / "backups.sqlite3"`: под exclusive catalog-migration lock скопировать consistent DB через SQLite backup API во временный sibling durable path, fsync/atomic replace, выставить `0600`, затем мигрировать schema. После успешной миграции все opens используют только durable path; legacy DB не удаляется автоматически и `doctor` показывает его как migrated legacy artifact. Если durable и legacy DB уже существуют, durable является authoritative, а automatic merge запрещён и диагностируется.

### D17: `environments` table

Минимальные поля:

```text
id
name
repository_root
git_common_dir
branch
base_ref
worktree_path
generated_config_path
python_environment_path
python_environment_owned    boolean
dependency_lock_path
http_interface
http_port
db_mode
source_db_name
target_db_name
backup_id                 nullable FK -> backups.id
runtime_json              versioned non-secret Odoo/Python/cwd/dependency snapshot
state                     creating|ready|failed|removing|cleanup_failed|removed
created_at
last_used_at
removed_at                nullable
last_error                nullable, sanitized and length-limited
```

Constraints:

- одна active environment на `(git_common_dir, branch)`;
- один `http_port` на active environment;
- `copy` требует target DB и backup ID;
- `shared` запрещает owned target DB/backup semantics;
- reused Python environment имеет `python_environment_owned=false` и не является cleanup target;
- secrets и содержимое config в SQLite не сохраняются.

### D18: `environment_events` table

Append-only row: `sequence`, `environment_id`, `operation` (`checkout|sync|use|shell|remove`), `outcome` (`started|succeeded|failed`), `occurred_at`, optional sanitized `message`. Exact ownership живёт в environment columns, а не в event taxonomy.

`last_used_at` и event `operation=use,outcome=succeeded` обновляются перед SDK-managed runtime operation; это не доказательство отсутствия ручной работы.

Записи environment и events после удаления остаются для SQL-аудита. Это локальный операционный аудит, не tamper-proof compliance log.

### D19: Top-level `run` and Instance ownership

```
┌──────────────────────────────────────────────────────────────────┐
│  odcli run                                                       │
│  1. Resolve ready environment + verify worktree/config/python    │
│  2. Compare dependency fingerprint → sync if changed             │
│  3. socket.bind((http_interface, http_port)) port check          │
│  4. If port busy → port-conflict/ownership-unknown, no 2nd proc  │
│  5. Update last_used_at + use/succeeded event                    │
│  6. Build instance via client.instance.from_environment(env)     │
│  7. instance.run_foreground() → return exit code                 │
│  8. Ctrl+C → stop only this foreground process group, exit 130   │
└──────────────────────────────────────────────────────────────────┘
```

Environment resource не запускает, не останавливает и не регистрирует процессы. CLI не дублирует этот lifecycle: после preflight/lock он только вызывает `from_environment()` и `run_foreground()`, затем возвращает exit code. Environment отвечает за persisted port/config, runtime полностью принадлежит `OdooInstance`.

`OdooInstance.run_foreground(config: StartConfig | None = None, *, cwd=None, env=None) -> int`:

- использовать тот же resolved command-prefix/config/process-group lifecycle, что и `start()`/`stop()`;
- наследовать stdout/stderr, поэтому Odoo logs идут прямо в terminal без буферизации, SQLite-хранения, tail API или собственного форматирования;
- блокироваться до завершения Odoo и возвращать exit code;
- на Ctrl+C корректно остановить owned process group.

Существующий `OdooInstance.run(args) -> CommandResult` остаётся captured one-shot API без изменения семантики. Не перегружать его неявным выбором между capture и foreground server mode.

### D20: `shell`

```bash
# Environment and DB are inferred from current registered worktree
odcli shell

# Explicit selector; extra args pass through after `--`
odcli --env <environment-id> shell -- --log-level=debug
```

Алгоритм:

1. Выполнить тот же selector/config/Python/dependency preflight, что и `run`, без HTTP port check.
2. Использовать БД, привязанную к environment: source DB для `shared`, target DB для `copy`.
3. Построить обычный `OdooInstance` через `from_environment()`.
4. Вызвать `OdooInstance.shell()` с `[recorded-python, odoo-bin]`, одним config/DB. Passthrough config/database overrides, включая attached `-cPATH`/`-dDB`, запрещены.
5. Наследовать stdin/stdout/stderr, signals и exit code штатного `odoo-bin shell`; не добавлять собственный REPL и не интерпретировать ввод.

`OdooInstance.shell()` и `run_foreground()` используют один internal foreground subprocess primitive, но остаются двумя ясными public operations. Существующий `run()` не перегружается третьим режимом. `EnvironmentResource` не получает runtime methods `run()`, `shell()`, `start()` или `stop()`.

### D21: `run_shell_script` — captured automation primitive

Для non-interactive automation MVP SDK добавляет captured primitive. Post-MVP CLI coordinators используют его; сам primitive входит в MVP public instance API, чтобы не плодить второй captured runner позже:

```python
def run_shell_script(
    source: str,
    *,
    argv: Sequence[str] = (),
    timeout: float | None = None,
    commit: bool = False,
) -> CommandResult
```

Primitive возвращает existing captured `CommandResult`. Bundled wrapper отделяет payload nonce-framed record, а private CLI coordinator разбирает его из stdout. Primitive добавляет один bound config/DB и non-TTY stdin; script `argv` инъецируется после Odoo parsing и не может менять binding. Все post-MVP automation commands используют этот primitive; interactive shell остаётся raw. В MVP CLI `eval`/`exec` не появляются, но `run_shell_script()` тестируется как SDK method.

### D22: Port registry

Port registry не гарантирует OS-level reservation между checkout и run. Свободный port автоматически выбирается только во время checkout до первого запуска. Повторная `socket.bind()`-проверка перед process start обязательна; занятый binding никогда не переназначается автоматически, а редкая TOCTOU-гонка завершается обычным non-zero exit Odoo, без port daemon и фонового lock service. Изменение порта существующего environment остаётся отдельной future explicit operation, потому что без persisted owned process handle SDK не может безопасно доказать, что старый Odoo остановлен.

### D23: Operation locks

Не держать SQLite transaction во время Git/uv/Odoo/DB operations. На Unix использовать stdlib `fcntl.flock(fd, LOCK_SH|LOCK_EX|LOCK_NB)` над deterministic files в `user_state_dir("odoo-instance-sdk")/locks`:

- один catalog-migration lock;
- project+branch provisioning lock для checkout до появления environment ID;
- один per-environment lock.

`run` и interactive shell получают `LOCK_SH`; sync/remove — `LOCK_EX`. Post-MVP eval/exec и module reads — `LOCK_SH`; update/test/export и commit automation — `LOCK_EX`. Conflict fail-fast. Kernel освобождает lock при normal exit и SIGKILL, поэтому stale-lock protocol, break command, PID recovery и writer-intent logic не нужны; lock file может оставаться как безвредный inode.

Lock files и `flock` — internal implementation `EnvironmentResource`/instance runtime, не public module и не CLI `LockManager`. CLI не вызывает lock API напрямую.

Lock защищает только SDK-managed artifacts, не PostgreSQL transactions или external processes. Произвольный shell остаётся mutation escape hatch. Windows locking откладывается до фактического требования поддержки Windows.

### D24: Stable machine output

Bounded leaf commands принимают один `--json` после command (`odcli env list --json`). Без него выводят human table/text. `run` и interactive `shell` raw-streaming и `--json` не принимают. Agent automation использует captured commands.

JSON stdout содержит ровно один versioned envelope и никакого progress/log text:

```json
{
  "schema_version": 1,
  "ok": true,
  "command": "env.list",
  "context": {"project_source": "cwd", "environment_source": null},
  "data": {},
  "warnings": []
}
```

Structured error содержит stable `error.code`, message и optional hint/details; progress/external logs идут stderr, secrets redacted. Exit codes: `0` success, `1` failure, Click `2` usage, `130` interrupt. Raw run/shell передают Odoo streams как есть.

### D25: `doctor`

```bash
odcli doctor
odcli doctor --json
odcli --project /path/to/repo doctor
```

Read-only checks покрывают manifest, worktrees, `uv`, recorded Python/ownership, dependencies, Odoo/config, catalog, DB/backups, ports и orphaned artifacts.

`doctor` — CLI coordinator над `list`/`get`/`history` и filesystem checks. Это не `client.doctor` и не public resource.

Errors дают non-zero; warnings остаются в output. `doctor --fix` не добавляется.

### D26: Checkout dry-run

`env checkout --dry-run` показывает worktree/config/port/DB plan, Python mode (`reuse|create`) и ownership, dependency inputs и helper argv. Ничего не создаёт; candidate values перепроверяются при execution.

### D27: `env list`

```bash
odcli env list
odcli env list --all
odcli env list --older-than 14d
odcli env list --json
```

Default table:

```text
ID  NAME  STATE  OBSERVED  BRANCH  PYTHON_MODE  DB_MODE  DATABASE  PORT  LAST_USED  WORKTREE
```

По умолчанию скрываются только `removed`; `failed` и `cleanup_failed` видны.

Quick reconciliation проверяет:

- наличие worktree в `git worktree list --porcelain -z`;
- наличие generated config;
- наличие recorded Python, ownership-consistent path и dependency lock/fingerprint;
- allocated port state;
- наличие owned backup в catalog/filesystem.

`OBSERVED` — `port-free|port-occupied|unknown`, не process ownership. Odoo health/DB reachability принадлежат `doctor`.

`--older-than` фильтрует кандидатов по `last_used_at` и не удаляет их автоматически.

### D28: `env remove`

```bash
odcli env remove <environment-id> --dry-run
odcli env remove <environment-id> --yes
```

Перед изменениями показать план и выполнить полный preflight. Без `--yes` требуется Click confirmation. Для агента `--yes` является явным подтверждением exact environment ID.

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
- любой занятый reserved address блокирует удаление как ownership-unknown; HTTP response служит только диагностикой;
- использовать `git worktree remove`, не recursive filesystem deletion;
- generated lock удалять по recorded environment path; Python venv — только при `python_environment_owned=true` и containment внутри environment root;
- reused project venv (`owned=false`) никогда не изменять во время remove;
- не использовать Git force и не удалять branch;
- drop разрешён только для `copy` environment с совпавшими cluster identity, target DB и recorded restore/backup ownership;
- shared source DB не удаляется ни при каких flags;
- `BackupResource.delete()` используется только для recorded environment-owned backup;
- отсутствие уже удалённого owned artifact считается идемпотентным успехом и записывается в audit;
- частичная ошибка оставляет `cleanup_failed` с точной причиной; повторный `remove` продолжает с оставшихся owned artifacts;
- `removed` ставится только после подтверждения отсутствия всех owned artifacts;
- final empty environment directory удаляется, SQLite rows остаются.

Bulk prune, автоматическое удаление по возрасту и `--force` для грязных worktrees не входят в scope.

### D29: Required public models/resources

Добавить минимально необходимые provisioning types. Это полный public increment MVP. Post-MVP команды новых public types не добавляют.

- `ProjectConfig`
- `DevelopmentEnvironment`
- `EnvironmentCheckoutOptions`
- `EnvironmentState`
- `EnvironmentDatabaseMode`
- `EnvironmentEvent`
- `EnvironmentResource` exposed as `OdooClient.environments`
- `InstanceFactory.from_environment(environment) -> OdooInstance`
- `InstanceConfig.command_prefix`/`InstanceConfig.default_cwd`
- `OdooInstance.run_foreground(...) -> int`
- `OdooInstance.shell(...) -> int`
- `OdooInstance.run_shell_script(...) -> CommandResult`

Отдельный Python resource не нужен: `ProjectConfig` задаёт default binding, `DevelopmentEnvironment` хранит resolved path и ownership flag.

Не добавлять: `GitWorktree`, `PythonVenv`, `LockManager`, `ModuleResource`, `TranslationResource`, environment-specific process wrapper, interfaces/factories/repositories для единственной SQLite-реализации, второй catalog file. Catalog остаётся internal persistence primitive; resource возвращает typed `msgspec.Struct` models, а runtime API остаётся на существующем `OdooInstance`. `OdooClient` экспортирует только `instance`, `backups`, `environments`.

Minimal public contract:

```python
EnvironmentSelector = str | DevelopmentEnvironment  # UUID or exact name; ambiguity is an error

project = ProjectConfig.load(project_path)

class EnvironmentCheckoutOptions(msgspec.Struct, frozen=True):
    base_ref: str | None = None
    name: str | None = None
    config_path: Path | None = None
    db_mode: EnvironmentDatabaseMode = EnvironmentDatabaseMode.SHARED
    source_database: str | None = None
    target_database: str | None = None
    odoo_bin: Path | None = None
    python: str | Path | None = None
    create_venv: bool = False
    http_port: int | None = None

class EnvironmentResource:
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

Click/SDK используют один checkout-options type; dry-run plan internal. Public errors: existing `ConfigError`, `EnvironmentNotFoundError`, `EnvironmentConflictError(code, details)`. Selector не выбирается по recency и не выбирается по «единственному ready».

### D30: Dependencies

### Add

```toml
"click>=8.2,<9"
"json5>=0.15,<1"
```

Click даёт CLI/testing, `json5` читает JSONC. TOML/locks/process/filesystem — stdlib; `uv` остаётсexternal tool.

### Do not add

- GitPython/Dulwich/pygit2, virtualenv manager, ORM/config/port libraries: достаточно system Git, `uv`, `sqlite3`, configparser/pathlib и socket;
- второй SQLite или отдельный environment catalog;
- psutil/process daemon/log DB: без persisted handle PID не доказывает ownership; raw foreground runtime остаётся в `OdooInstance`.

## Failure and Rollback Semantics

- `DevelopmentEnvironment` row создаётся в `creating` до первого owned artifact.
- Exact owned paths/names фиксируются в columns до создания; generic checkout outcome фиксируется event row.
- При checkout failure выполняется best-effort cleanup только уже созданных и доказанно owned artifacts.
- Если rollback полный, environment остаётся `failed` только как audit row; если неполный — `cleanup_failed` и виден в обычном `list`.
- Ошибки и events не содержат passwords, config body или environment variables.
- Повторный checkout для уже active repo+branch возвращает существующее matching environment либо конфликт; дубликат не создаётся.

## Post-MVP Design Contracts

Post-MVP команды не входят в первый CLI и не добавляют public resources. Дизайн ниже сохраняется как контракт для slices 6-7.

### `eval` and `exec`

```bash
odcli eval "env['res.users'].search_count([])"
odcli exec ./script.py -- arg1 arg2
```

- `eval` вычисляет одно Python expression в Odoo shell context (`env`, `odoo`, `self`) и возвращает scalar/collection JSON либо typed recordset summary `{model, ids, count}`; unknown objects получают bounded sanitized `repr`.
- `exec` читает explicit file (`-` означает caller stdin), передаёт script через shell stdin и устанавливает predictable `sys.argv` из tokens после `--`.
- default — best-effort shell rollback. Explicit `--commit` виден в plan и generic shell event message, но не является security boundary: script/Odoo method может commit'ить сам, что help обязан предупреждать.
- arbitrary code execution является осознанной local developer capability; project config не может автоматически подставлять eval/exec source или запускать его hook'ом.

### `module` commands

```bash
odcli module list --state installed
odcli module list comerta_base sale
odcli module update comerta_base --dry-run
odcli module update comerta_base --yes
odcli module test comerta_base --test-tags /comerta_base --reload-tests
```

- `list [MODULE...]` читает `ir.module.module`; optional names фильтруют exact modules, `--state` фильтрует state.
- `update` требует installed modules, lifecycle lock, dry-run plan и explicit `--yes`; внутри shell вызывает `button_immediate_upgrade()`, который сам commit'ит module operation и rebuild'ит registry. Общий shell rollback не выдаётся за rollback module update.
- `test` вызывает Odoo 19 `odoo.tests.shell.run_tests(env, test_tags, modules, reload_tests=...)`; workers должны быть `0`. Этот runner вызывает `server.http_spawn()`, если server ещё не создан, поэтому команда под exclusive artifact lock отдельно требует свободный bound HTTP port. Port conflict даёт deterministic precondition error без автоматического переназначения. Exit non-zero при failed tests **и** при zero tests unless `--allow-empty` указан явно.
- module names валидируются до mutating call; partial multi-module update не обещает transactional rollback сверх гарантий самого Odoo.
- install/uninstall не добавляются: это отдельные destructive semantics.
- public `ModuleResource` не добавляется; coordinator остаётся private application layer.

### CI translation export through Odoo shell

```bash
odcli translations export --module comerta_base --language ru_RU
odcli translations export --module comerta_base --language ru_RU
git diff --exit-code -- comerta_base/i18n
```

Команда запускает штатный `odoo-bin shell` через recorded Python/config/DB и подаёт exporter через **non-TTY stdin**.

Не использовать `--shell-file` для headless execution: в Odoo 19 shell читает `--shell-file` только в interactive TTY branch, а при non-TTY исполняет `sys.stdin.read()`.

Algorithm:

1. Разрешить environment, проверить recorded Python/dependencies/config и получить bound DB.
2. По worktree-local `addons_path` и `__manifest__.py` найти единственный module root для каждого `--module` и потребовать installed module; неоднозначный technical name блокирует запись. Existing `i18n` не обязателен: real export безопасно создаёт `<module-root>/i18n`, dry-run только планирует создание.
3. До создания каталога доказать, что resolved module root и target paths остаются внутри worktree-local addon root, не проходят через escaping symlink и не указывают на external path.
4. Вызвать `run_shell_script()` с exporter source на non-TTY stdin и captured stdout/stderr/machine result. Coordinator redacts/sanitizes diagnostics и только затем выводит их в CLI stderr; direct inherited stderr разрешён лишь interactive `shell`.
5. В shell использовать Odoo `env` (`SUPERUSER_ID`) и `base.language.export`: `__new__` для `<module>.pot`, active language, `format=po`, `export_type=module`.
6. Фактическое имя PO брать из validated wizard `name`/`tools.get_iso_codes()`, а не из raw `res.lang.code`: например, request `ru_RU` штатно пишет `ru.po`. Имя должно быть basename с expected `.po`; path separators/escape запрещены.
7. Проверить installed module, active language, non-empty valid base64 payload и записать файл атомарно, сохраняя mode существующего файла.
8. Вернуть summary с requested `res.lang.code`, actual filename/path и missing counts; partial failure даёт non-zero exit.
9. Не вызывать commit из bundled exporter; финальный rollback очищает transient wizard records, если вызываемый Odoo code сам не commit'ил.

Command options:

```text
--module TEXT       repeatable, required
--language TEXT     repeatable res.lang.code, default: ru_RU
--json              stable machine-readable summary
```

Команда не требует запущенного HTTP server, host, port, Odoo login или Odoo password. PostgreSQL доступ и возможные DB credentials по-прежнему берутся из generated `odoo.conf`; формулировка «без credentials» относится именно к user-level XML-RPC authentication.

Граница ответственности:

- `DevelopmentEnvironment` разрешает worktree, module output paths и bound DB;
- `OdooInstance.run_shell_script()` обеспечивает Odoo registry/env, captured child lifecycle и machine-result framing;
- translation-export coordinator формирует validated export spec и файлы;
- отдельный public `TranslationResource` не добавляется никогда в этом issue; достаточно private CLI/application coordinator поверх `run_shell_script()`.

### Declared addon dependency verification

```bash
odcli deps verify
odcli deps verify --json
```

Команда разрешает environment, запускает `uv pip check` для installed distributions и проверяет imports из безопасно разобранных addon `external_dependencies['python']` в managed interpreter. Manifest Python не исполняется; missing import возвращает module/import name.

### VS Code generation

Post-MVP. Не входит в первый CLI и не блокирует MVP slices 1-5.

`odcli vscode generate` выполняет обратное преобразование current project/environment в debugpy launch profile:

```bash
odcli vscode generate          # print one profile
odcli vscode generate --write  # create launch.json only when absent
```

Generated profile содержит recorded Python/program, config, DB/port, portable `cwd`, integrated terminal и `justMyCode=false`; secrets/tasks/mutating args исключены.

Команда требует ready environment. Default ничего не пишет; `--write` атомарно создаёт отсутствующий `.vscode/launch.json`, но отказывается merge/rewrite существующего JSONC.

## Risks / Trade-offs

- **[Schema v2 → v3 миграция + path migration на существующих инсталляциях]** → one-time cache→data migration под exclusive catalog-migration lock; legacy DB не удаляется автоматически, `doctor` показывает как migrated legacy artifact; durable authoritative при конфликте, automatic merge запрещён.
- **[Reuse venv = shared dependency mutations между worktrees]** → осознанное решение; isolation требует explicit `--create-venv`.
- **[Port registry не гарантирует OS-level reservation между checkout и run]** → повторная `socket.bind()`-проверка перед process start обязательна; TOCTOU-гонка завершается обычным non-zero exit Odoo.
- **[No persisted process handle → нельзя доказать ownership Odoo на address]** → responsive Odoo на address не доказывает принадлежность этому environment; занятый port = deterministic `port-conflict`/ownership-unknown без запуска второго process.
- **[fcntl Unix-only]** → MVP; Windows locking откладывается.
- **[Checkout failure оставляет `failed`/`cleanup_failed` environment]** → аудируемо; повторяемый `remove` продолжает с оставшихся owned artifacts.

## Open Questions

Нет. Все ключевые решения зафиксированы в issue #3 и отражены выше.

## References

- [Git worktree porcelain format](https://git-scm.com/docs/git-worktree)
- [VS Code debug configuration](https://code.visualstudio.com/docs/debugtest/debugging-configuration)