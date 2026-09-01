## Purpose

Odoo process lifecycle and foreground, shell, and captured script execution on OdooInstance.
## Requirements
### Requirement: Server lifecycle в instance

`OdooInstance` MUST предоставлять методы `run()`, `start()`, `stop()`, `status()`, `wait_ready()`, `run_foreground()`, `iter_logs()`, `shell()` и `run_shell_script()` напрямую, без вложенного подресурса `instance.server`.

Process registry (зарегистрированные `OdooProcess` и subprocess handles) MUST храниться приватно на `OdooClient` и разделяться всеми instances. Публичный `client.server` MUST NOT существовать.

`instance.run()`, `start()`, `stop()`, `status()`, `run_foreground()`, `shell()`, `run_shell_script()` MUST использовать instance `command_prefix` (если set), затем client fallback на `OdooClientConfig.executable`.

`instance.start(config: StartConfig)` MUST принимать `StartConfig` и возвращать `OdooProcess`. `StartConfig` остаётся `msgspec.Struct` с `forbid_unknown_fields=True` и полем `logfile: str | None`. Метакласс `_StructMeta` удаляется.

Существующий `OdooInstance.run(args) -> CommandResult` остаётся captured one-shot API без изменения семантики. Не перегружать его неявным выбором между capture и foreground server mode.

`shell()` и `run_foreground()` используют один internal foreground subprocess primitive, но остаются двумя ясными public operations. `EnvironmentResource` не получает runtime methods `run()`, `shell()`, `start()` или `stop()`.

#### Scenario: Instance prefix used over client fallback

- **WHEN** `instance` создан через `from_environment()` с `command_prefix=["/venv/bin/python", "/worktree/odoo-bin"]`
- **THEN** `run()`/`start()`/`run_foreground()`/`shell()`/`run_shell_script()` используют prefix, не `OdooClientConfig.executable`

#### Scenario: Client fallback for manual instance

- **WHEN** `instance` создан через `instance(base_url=...)` без `command_prefix`
- **THEN** `run()`/`start()` используют `OdooClientConfig.executable` как fallback

#### Scenario: Запуск сервера через instance

- **WHEN** пользователь вызывает `instance.start(config)`
- **THEN** Odoo executable запускается, процесс регистрируется в общем registry на `OdooClient`, и возвращается `OdooProcess`

#### Scenario: Общий registry между instances

- **WHEN** два instance запускают по одному процессу через `instance_a.start(...)` и `instance_b.start(...)`
- **THEN** оба процесса зарегистрированы в одном registry на `OdooClient` и доступны через `instance_a.status(proc_a)` и `instance_b.status(proc_b)`

### Requirement: `OdooInstance.run_foreground()`

`OdooInstance.run_foreground(config: StartConfig | None = None, *, cwd=None, env=None) -> int` MUST:

- если `config is None`, использовать `self.config.start_config` (from `from_config()`/`from_environment()`); если `start_config` is None → `InstanceConfigurationError`;
- использовать тот же resolved command-prefix/config/process-group lifecycle, что и `start()`/`stop()`;
- наследовать stdout/stderr, поэтому live Odoo output идёт прямо в terminal без буферизации, SQLite-хранения или собственного форматирования; tail native logfile — отдельный `iter_logs()`, не этот метод;
- блокироваться до завершения Odoo и возвращать exit code;
- на Ctrl+C корректно остановить owned process group.

После spawn (только для instance bound к environment через `from_environment()`) `run_foreground()` MUST persist current runtime identity в catalog `environment_runtime` (`root_pid`, `create_time`, `started_at`, branch/commit, `http_url`/`http_port`, `database_name`). В `finally` MUST очистить runtime identity (`clear_environment_runtime`) best-effort. Manual instance — без persist. `shell()`/`run_shell_script()`/`start()`/`stop()` — без persist (только `run_foreground`).

#### Scenario: Foreground run with explicit config

- **WHEN** `instance.run_foreground(config=cfg)` and Odoo exits with code 0
- **THEN** returns `0`, runtime identity is cleared in catalog `finally`

#### Scenario: Foreground run uses start_config

- **WHEN** `instance.run_foreground()` (config=None) и instance создан через `from_environment()` со `start_config` from generated `odoo.conf`
- **THEN** uses `self.config.start_config`

#### Scenario: Foreground run no start_config — error

- **WHEN** `instance.run_foreground()` (config=None) и `self.config.start_config is None`
- **THEN** `InstanceConfigurationError`

#### Scenario: Ctrl+C stops process group

- **WHEN** `instance.run_foreground()` получает Ctrl+C
- **THEN** owned process group stopped, runtime identity cleared in `finally`, CLI exits 130

#### Scenario: Manual instance does not persist runtime identity

- **WHEN** `client.instance("http://localhost:8069").run_foreground()` (no environment_id)
- **THEN** no `environment_runtime` row is written or cleared

#### Scenario: Shell does not persist runtime identity

- **WHEN** `instance.shell()` executes
- **THEN** no `environment_runtime` row is written (only `run_foreground` persists identity)

### Requirement: `OdooInstance.iter_logs()`

`OdooInstance.iter_logs(*, tail: int = 100, follow: bool = False) -> Iterator[str]` MUST:

- принадлежать `OdooInstance`, не `DevelopmentEnvironment`;
- читать только `StartConfig.logfile` (не произвольный второй path);
- резолвить relative value через тот же runtime cwd, что используется для старта Odoo (`default_cwd` или process cwd);
- возвращать ровно последние `N` строк из readable logfile;
- при `follow=True` стримить appended lines и продолжать после truncation/file replacement, reopen того же configured path, не сканировать rotated filenames;
- при `tail < 1`, absent/empty `logfile`, missing/unreadable file или отсутствии `start_config` поднимать `InstanceConfigurationError` с path/reason;
- не создавать файл, не менять `run_foreground()` streams и не запускать Postgres preflight.

Stdlib only: `pathlib`, `collections.deque`, file iteration, small sleep while following.

#### Scenario: Tail last N lines

- **WHEN** `instance.iter_logs(tail=3)` and the configured logfile has 5 lines
- **THEN** yields exactly the last 3 lines

#### Scenario: Follow after append

- **WHEN** `instance.iter_logs(follow=True)` and a line is appended
- **THEN** the new line is yielded

#### Scenario: Follow after truncation or replacement

- **WHEN** follow is active and the file is truncated or replaced at the same path
- **THEN** iteration continues from the reopened configured path

#### Scenario: Missing logfile

- **WHEN** `logfile` is unset, empty, missing or unreadable
- **THEN** `InstanceConfigurationError` with the resolved path; no file is created

### Requirement: `OdooInstance.shell()`

`OdooInstance.shell(*, args: Sequence[str] = ()) -> int` MUST:

- использовать тот же internal foreground subprocess primitive, что и `run_foreground()`;
- использовать `self.config.start_config` (from `from_config()`/`from_environment()`) для bound config/DB; если `start_config is None` → `InstanceConfigurationError`;
- `args` — passthrough Odoo args (e.g. `--log-level=debug`); передаются после `odoo-bin shell` subcommand; passthrough config/database overrides MUST быть запрещены и вызывать error — как attached form (`-cPATH`/`-dDB`), так и spaced form (`-c PATH`/`-d DB`/`--config PATH`/`--database NAME`);
- наследовать stdin/stdout/stderr, signals и exit code штатного `odoo-bin shell`;
- not add собственный REPL и not интерпретировать ввод.

`shell()` и `run_foreground()` — две ясные public operations, один internal primitive. Существующий `run()` не перегружается третьим режимом.

#### Scenario: Shell uses start_config

- **WHEN** `instance.shell()` и instance создан через `from_environment()` со `start_config` from generated `odoo.conf`
- **THEN** uses `self.config.start_config` for bound config/DB

#### Scenario: Shell no start_config — error

- **WHEN** `instance.shell()` и `self.config.start_config is None`
- **THEN** `InstanceConfigurationError`

#### Scenario: Shell inherits stdio

- **WHEN** `instance.shell()` executes
- **THEN** stdin/stdout/stderr inherited from parent, `odoo-bin shell` runs interactively

#### Scenario: Passthrough config override forbidden (attached and spaced)

- **WHEN** `shell(args=["-cPATH"])` or `shell(args=["-c", "PATH"])` or `shell(args=["--config", "PATH"])` or `shell(args=["-dDB"])` or `shell(args=["-d", "DB"])` or `shell(args=["--database", "DB"])`
- **THEN** error, binding cannot be overridden

### Requirement: `OdooInstance.run_shell_script()`

`OdooInstance.run_shell_script(source: str, *, argv: Sequence[str] = (), timeout: float | None = None, commit: bool = False) -> CommandResult` MUST:

- возвращать existing captured `CommandResult`;
- использовать `self.config.start_config` для bound config/DB; если `start_config is None` → `InstanceConfigurationError`;
- добавлять non-TTY stdin (script source);
- inject script `argv` after Odoo parsing; `argv` не может менять binding;
- bundled wrapper отделяет payload nonce-framed record, private CLI coordinator разбирает его из stdout.

`commit` semantics:

- `commit=False` (default) — best-effort shell rollback в конце; warning: script/Odoo method MAY commit самостоятельно, `commit=False` не является security boundary;
- `commit=True` — explicit commit в конце; visible в plan/event message; не security boundary.

`eval`/`exec`/`module`/`translations` используют этот primitive. Interactive shell остаётся raw.

#### Scenario: Captured script result

- **WHEN** `run_shell_script("print(1+1)")` executes
- **THEN** returns `CommandResult` with captured stdout/stderr/returncode

#### Scenario: argv injected after Odoo parsing

- **WHEN** `run_shell_script(source, argv=["--flag"])` executes
- **THEN** `--flag` injected after Odoo parsing, cannot change bound config/DB

#### Scenario: commit=False best-effort rollback

- **WHEN** `run_shell_script(source, commit=False)` and script does not self-commit
- **THEN** best-effort rollback at end; transient records cleaned

#### Scenario: commit=True explicit commit

- **WHEN** `run_shell_script(source, commit=True)`
- **THEN** explicit commit at end; visible in event message

### Requirement: Inspectable Odoo lifecycle commands

`OdooInstance` process-spawning operations SHALL expose command siblings for captured run, background start, foreground run, interactive shell, shell-script execution, and process stop. `stop_command()` SHALL be present with the same public signature and return contract on every supported platform: its plan SHALL contain a captured `ProcessStep` for Windows `taskkill` and an honest `ActionStep` for POSIX signal/no-child termination. Existing methods SHALL delegate without changing return values, process registration, artifact locks, or readiness behavior.

#### Scenario: Stop command is inspected across platforms

- **WHEN** a caller constructs `stop_command()` on Windows or POSIX
- **THEN** the public method exists on both platforms with the same contract
- **AND** Windows plans the `taskkill` child process while POSIX plans the actual signal/no-child action

#### Scenario: Foreground command preserves TTY

- **WHEN** `run_foreground_command()` is run normally
- **THEN** the exact captured process inherits native stdio, owns its process group/session, and retains existing signal/exit behavior

#### Scenario: Shell script command preserves executable input

- **WHEN** `run_shell_script_command()` is inspected
- **THEN** its plan contains the real Odoo shell argv and exact redacted wrapper/source bytes sent through stdin
- **AND** commit or rollback intent is explicit

### Requirement: Lifecycle cleanup remains explicit

Process registration, handle ownership, signal forwarding, TERM/KILL/reap, generated secret-config cleanup, and artifact locking SHALL remain explicit lifecycle code. These effects SHALL NOT be modeled as Expression stages or a generic rollback workflow.

#### Scenario: Foreground wait raises

- **WHEN** waiting for a captured foreground handle raises an exception
- **THEN** the owned process group receives bounded cleanup and is reaped
- **AND** the original exception semantics are retained

