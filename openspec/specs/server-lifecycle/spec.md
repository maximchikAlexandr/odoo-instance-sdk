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

`OdooInstance.run_foreground(config: StartConfig | None = None, *, args: Sequence[str] = (), cwd=None, env=None) -> int` SHALL delegate exactly once to `run_foreground_command(config, args=args, cwd=cwd, env=env).run()`. `run_foreground_command()` SHALL expose the same keyword-only `args` parameter and SHALL:

- use `self.config.start_config` when `config is None`; when both are absent, raise `InstanceConfigurationError`;
- use the same resolved command prefix, generated config arguments, dependency preflight, artifact lock, process-group lifecycle, and cleanup as `start()`/`stop()`;
- freeze the caller-supplied sequence as an ordered tuple during command construction, validate it once through the same runtime-argument validator used by `shell_command()`, and append it after the generated config arguments in the single captured foreground `ProcessStep`;
- preserve each allowed argument as one argv element without shell interpolation, normalization, deduplication, reordering, or reconstruction during `.run()`;
- reject protected environment-binding overrides in exact spaced, long `--name=value`, Odoo-recognizable abbreviated-long, and attached short forms before creating the foreground step or spawning a child. The protected names SHALL be `-c`/`--config`, `-d`/`--database`, `--db-filter`, `-r`/`--db_user`, `-w`/`--db_password`, `--db_host`, `--db_port`, `--db_sslmode`, `--addons-path`, `--upgrade-path`, `--data-dir`, `--http-interface`, `--http-port`, `--gevent-port`, `--longpolling-port`, and `--logfile`;
- allow other native runtime arguments, including repeated `--dev`, `--log-level`, `--workers`, and `--stop-after-init` values;
- inherit stdin/stdout/stderr so live Odoo output remains native and unbuffered, block until Odoo exits, and return its actual exit code;
- stop the owned process group correctly on Ctrl+C.

For every token beginning `--`, the validator SHALL compare the option-name portion before the first `=` with the protected long names. It SHALL reject an exact match and every non-empty proper prefix of a protected name, regardless of whether that prefix is ambiguous or unknown in the installed Odoo version, so Odoo `optparse` abbreviation cannot bypass the boundary. It SHALL not reject a longer near-prefix that no protected name starts with. It SHALL reject an exact short protected name or its attached value. It SHALL not implement or duplicate the complete Odoo option parser. `shell_command()` SHALL retain its subcommand placement (`... generated-config-args shell <args>`) while using this expanded shared protected-name boundary.

After spawn, only an instance bound through `from_environment()` SHALL persist current runtime identity in `environment_runtime` (`root_pid`, `create_time`, `started_at`, branch/commit, `http_url`/`http_port`, `database_name`). `run_foreground()` SHALL clear that identity best-effort in `finally`. Manual instances SHALL not persist it; `shell()`/`run_shell_script()`/`start()`/`stop()` SHALL not persist it.

#### Scenario: Foreground run with explicit config

- **WHEN** `instance.run_foreground(config=cfg)` runs and Odoo exits with code `0`
- **THEN** the method returns `0` and clears runtime identity in `finally`

#### Scenario: Foreground run uses start_config

- **WHEN** `instance.run_foreground()` is called on an instance created through `from_environment()` with a bound start config
- **THEN** the captured command uses `self.config.start_config`

#### Scenario: Foreground run no start_config — error

- **WHEN** `instance.run_foreground()` is called with `config=None` and `self.config.start_config is None`
- **THEN** it raises `InstanceConfigurationError` before child-process launch

#### Scenario: Allowed native arguments preserve boundaries and order

- **WHEN** `run_foreground_command(args=("--dev=reload", "--log-level", "debug", "--dev=xml", "--stop-after-init"))` is constructed
- **THEN** its foreground `ProcessStep.argv` contains those five exact elements, in that order, after the generated config arguments
- **AND** a recording executor receives the identical captured private argv when `.run()` executes

#### Scenario: Mutable caller input changes after capture

- **WHEN** a list passed as `args` is changed after `run_foreground_command()` returns
- **THEN** `.plan`, `.commands`, and the argv consumed by `.run()` remain unchanged

#### Scenario: Protected overrides fail closed

- **WHEN** native args contain any protected name in spaced form, `--name=value` form, or attached short form such as `-cPATH`, `-dDB`, `-rUSER`, or `-wSECRET`
- **THEN** command construction raises `InstanceConfigurationError` identifying the offending option
- **AND** no dependency preflight, artifact lock, secret-config write, runtime identity write, or child-process launch occurs

#### Scenario: Protected long-option abbreviation cannot bypass validation

- **WHEN** native args contain `--datab other`, `--datab=other`, or any other non-empty proper prefix of a protected long option
- **THEN** command construction raises `InstanceConfigurationError` before a foreground step or side effect exists
- **AND** a longer token such as `--database-extra` is not treated as an abbreviation of `--database`

#### Scenario: Shell and foreground share the protected boundary

- **WHEN** the same protected addons, data, database-connection, HTTP bind/port, or logfile override is passed to `shell_command(args=...)` or `run_foreground_command(args=...)`
- **THEN** both operations reject it through the same validator and neither constructs a process step

#### Scenario: Ctrl+C stops process group

- **WHEN** `instance.run_foreground(args=("--dev=reload",))` receives Ctrl+C
- **THEN** the owned process group is stopped, runtime identity is cleared in `finally`, and the CLI exits `130`

#### Scenario: Manual instance does not persist runtime identity

- **WHEN** `client.instance("http://localhost:8069").run_foreground(config=cfg, args=("--stop-after-init",))` executes
- **THEN** no `environment_runtime` row is written or cleared

#### Scenario: Shell does not persist runtime identity

- **WHEN** `instance.shell()` executes
- **THEN** no runtime identity is written because only foreground run owns that lifecycle

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

### Requirement: Foreground lifecycle persists either owner kind

`OdooInstance.run_foreground_command()` SHALL use the single runtime binding attached by `from_environment()` or `from_project()` to persist and clear the current process identity. This SHALL remain part of the explicit foreground lifecycle and SHALL not apply to manual instances, shell, shell-script, background start, or stop operations.

#### Scenario: Project and environment use one lifecycle
- **WHEN** equivalent foreground commands start from project-owned and environment-owned instances
- **THEN** both use the same spawn/cleanup path and differ only in the exclusive persisted owner identity

### Requirement: Eval and exec transport separates startup logs, user output, and results

The Odoo shell wrapper shared by eval and exec SHALL frame captured user stdout separately from startup stdout and the expression/script result. On user-code failure it SHALL retain the exception type, message, and relevant bounded traceback/source context even after long startup logs; on startup failure it SHALL classify the failure separately. Truncation SHALL be indicated and SHALL preferentially retain the exception and nearby failure context. A valid framed user-code exception SHALL map to CLI envelope v1 as `ok=false`, sanitized `error.message`, and `error.details` containing exactly `result=null`, bounded `user_stdout`, non-null structured `user_error`, and boolean `truncated`; top-level `result` and `data` SHALL be absent. The error code SHALL be `eval_user_code_failed` for eval and `exec_user_code_failed` for exec. A non-zero command without a valid framed user-code error SHALL map to `eval_startup_failed` or `exec_startup_failed`, respectively, without fabricated framed details. The existing shell execution boundary, rollback default, non-zero failures, and secret redaction SHALL remain unchanged.

#### Scenario: Print-only eval has a null result
- **WHEN** evaluated code prints Unicode/multiline text and returns no value
- **THEN** the typed result is null and the exact bounded user output is available separately

#### Scenario: Long startup log does not hide exception
- **WHEN** user code raises after startup emitted more data than the diagnostic bound
- **THEN** the failure envelope remains `ok=false`, `error.details.user_error` contains exception type/message and relevant failure context, `error.details.user_stdout` preserves bounded user output, and `error.details.truncated` is true

#### Scenario: Framed user exception and exit status agree
- **WHEN** eval or exec produces a valid framed user-code exception
- **THEN** Rich, JSON, and TOON classify it as failure and the CLI exits `1`
- **AND** machine output never reports `ok=true` for that non-zero user-code outcome

#### Scenario: Exec failure classification is command-specific
- **WHEN** exec produces a valid framed user-code exception or fails before producing one
- **THEN** the envelope uses `exec_user_code_failed` with exact framed `error.details` or `exec_startup_failed` without `error.details`, respectively

