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

After spawn, only an instance bound through `from_environment()` SHALL persist current runtime identity in `environment_runtime` (`root_pid`, exact `psutil.Process(root_pid).create_time()`, `started_at`, branch/commit, `http_url`/`http_port`, `database_name`). `run_foreground()` SHALL clear that identity best-effort in `finally`. If foreground wait raises unexpectedly, it SHALL terminate and reap the owned process group before clearing identity and re-raising, even when the leader exited but descendants remain; cleanup errors SHALL NOT mask the original exception. Manual instances SHALL not persist it; `shell()`/`run_shell_script()`/`start()`/`stop()` SHALL not persist it.

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

#### Scenario: Foreground identity is exact

- **WHEN** an environment-bound foreground process is spawned
- **THEN** its catalog `create_time` equals that process's exact `psutil.Process(pid).create_time()` value

#### Scenario: Unexpected wait failure reaps owned process

- **WHEN** the foreground wait raises after the leader exits but an owned descendant remains live
- **THEN** the group is terminated and reaped, runtime identity is cleared best-effort, and the original exception is re-raised

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

`OdooInstance` process-spawning operations SHALL expose command siblings for captured run, background start, foreground run, detached run, interactive shell, shell-script execution, and process stop. `stop_command()` SHALL be present with the same public signature and return contract on every supported platform: its plan SHALL contain a captured `ProcessStep` for Windows `taskkill` and an honest `ActionStep` for POSIX signal/no-child termination. Existing methods SHALL delegate without changing return values, process registration, artifact locks, or readiness behavior. The detached launch convenience method SHALL delegate to its `*_command()` sibling and SHALL NOT rebuild argv, cwd, environment, stdin, or actions.

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

#### Scenario: Detached command is inspectable

- **WHEN** a caller constructs the detached launch command
- **THEN** the captured plan includes the sanitized process command and detached lifecycle actions without spawning
- **AND** the convenience method delegates to that command without reconstructing argv

### Requirement: Lifecycle cleanup remains explicit

Process registration, handle ownership, signal forwarding, TERM/KILL/reap, generated secret-config cleanup, and artifact locking SHALL remain explicit lifecycle code. These effects SHALL NOT be modeled as Expression stages or a generic rollback workflow.

#### Scenario: Foreground wait raises

- **WHEN** waiting for a captured foreground handle raises an exception
- **THEN** the owned process group receives bounded cleanup and is reaped
- **AND** the original exception semantics are retained

### Requirement: Foreground lifecycle persists either owner kind

`OdooInstance.run_foreground_command()` SHALL use the single runtime binding attached by `from_environment()` or `from_project()` to persist and clear the current process identity. Detached launch SHALL use that same binding to persist identity after spawn and SHALL leave it in place until `stop()` or stale-runtime recognition; returning from the detached launcher SHALL NOT clear the record. This SHALL remain explicit process-lifecycle code and SHALL not apply to manual instances, shell, shell-script, background `start()`, or the stop operation itself.

#### Scenario: Project and environment use one lifecycle

- **WHEN** equivalent foreground commands start from project-owned and environment-owned instances
- **THEN** both use the same spawn/cleanup path and differ only in the exclusive persisted owner identity

#### Scenario: Detached launch persists the same binding

- **WHEN** a detached launch succeeds from a project-owned or environment-owned instance
- **THEN** the same runtime binding is persisted and remains after the launcher returns

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

### Requirement: Scripted shell transaction finalization

Every existing consumer of the shared scripted Odoo shell wrapper SHALL use one transaction-finalization contract. Successful user code SHALL commit only when `commit=True` and otherwise SHALL roll back. Failed user code SHALL never commit and SHALL attempt rollback. A commit or rollback failure SHALL be a non-successful result and SHALL remain distinguishable from a user-code failure after existing sanitization. The contract SHALL NOT claim to undo explicit commits performed by user code or external non-transactional effects.

#### Scenario: User code fails with commit requested

- **WHEN** scripted user code performs a conditional write, raises, and the caller requested commit
- **THEN** the wrapper calls rollback and does not call commit
- **AND** the process and bounded envelope report a user-code failure

#### Scenario: Commit fails after successful user code

- **WHEN** user code succeeds but transaction commit raises
- **THEN** the operation fails as transaction finalization rather than returning `ok=true`
- **AND** the sanitized commit failure remains available to Rich and machine projections

#### Scenario: Rollback fails after user-code failure

- **WHEN** user code raises and the required rollback also raises
- **THEN** the operation reports the user-code failure and a distinct rollback-finalization failure
- **AND** neither failure is suppressed

#### Scenario: Successful transaction modes remain compatible

- **WHEN** user code and the requested commit or rollback succeed
- **THEN** the wrapper returns the existing successful framed result with `transaction=commit` or `transaction=rollback`

#### Scenario: All scripted consumers share the wrapper

- **WHEN** eval, exec, module update/test, translations export, administrator reset, or database preparation executes scripted Odoo code
- **THEN** each operation uses this same wrapper and transaction outcome classification

### Requirement: Detached Odoo launch

`OdooInstance` SHALL expose a detached launch command sibling that spawns Odoo through `internal/proc`, confirms the process is alive, persists runtime identity, and returns without waiting for Odoo to finish. The convenience method SHALL delegate to that captured command and SHALL NOT rebuild argv, cwd, environment, stdin, or actions. Detached launch SHALL NOT overload `run_foreground_command()`, which continues to inherit stdio and block until Odoo exits. The detached mode SHALL use the existing project/environment resolution, port preflight, PostgreSQL preflight, argv construction, `internal/proc`, runtime identity, and ownership/stop mechanism. A separate daemon manager or second command-construction path SHALL NOT be introduced.

After spawn, the command SHALL return PID, project/environment identity, HTTP endpoint, and log path. If the process exits immediately, the command SHALL return an error and SHALL NOT leave a false `running` record. The Odoo lifetime SHALL NOT be tied to the terminal or CLI: exiting the launcher SHALL NOT terminate the child. Stopping SHALL work through the existing `stop()` and stale runtime records SHALL be recognized normally.

Logs SHALL be written to the path from `resolve_effective_logfile()` in `resources/instance/runtime.py` (see `cli-odcli` `odcli run`). An unwritable fallback SHALL fail before spawn with `logfile_unwritable` and the exact path. `iter_logs` SHALL read that path. `--dry-run` SHALL show the sanitized command, detached plan, and logfile path without spawning or creating the file.

#### Scenario: Detached launch returns promptly

- **WHEN** a detached launch succeeds
- **THEN** the command returns promptly with PID, identity, endpoint, and log path while Odoo continues running

#### Scenario: Immediate exit is not success

- **WHEN** the detached Odoo process exits immediately after spawn
- **THEN** the command returns an error and leaves no stale active runtime record

#### Scenario: Stop targets the detached runtime

- **WHEN** `stop()` runs after a detached launch
- **THEN** it stops exactly that persisted runtime

#### Scenario: Logs work after detached launch

- **WHEN** `iter_logs(tail=...)` and `iter_logs(follow=True)` run after a detached launch
- **THEN** both read from the resolved logfile and follow produces new lines

#### Scenario: Detached dry-run does not spawn

- **WHEN** a detached dry-run is requested
- **THEN** the sanitized process command, detached lifecycle plan, and resolved logfile path are shown and no process, runtime record, directory, or file is created

#### Scenario: No logfile fails fast

- **WHEN** a detached launch is requested with no configured logfile
- **THEN** the command fails before spawn with a clear diagnostic

#### Scenario: Detached launch provisions an effective logfile

- **WHEN** a detached launch is requested with no configured `logfile`
- **THEN** `odoo.log` next to the effective config is created before spawn and used, without editing the user's `odoo.conf`

#### Scenario: Unwritable logfile fails fast

- **WHEN** the chosen fallback logfile path cannot be created or opened
- **THEN** the command fails before spawn with a clear diagnostic and the exact path

### Requirement: Long-restore Rich stages and elapsed time

Long-running restore SHALL publish stages through existing `StepObserver`/`StepEvent` and Rich Live: ids `backup_prepare`, `auxiliary_start`, `db_restore`, `db_verify`, `filestore_restore`, `admin_reset`, `default_switch`. Heartbeat SHALL emit `StepEvent` every 0.5 s without child stdout. Percent SHALL be shown only when streaming dump/filestore bytes provide a total. On error, the safe primary cause, last stage id, and elapsed seconds SHALL be preserved. JSON/TOON SHALL keep one document; no heartbeat events on machine stdout.

#### Scenario: Current stage appears immediately

- **WHEN** a long restore starts in Rich
- **THEN** the current stage appears immediately after the operation begins

#### Scenario: Spinner and elapsed time update without child stdout

- **WHEN** a long blocking stage is in progress
- **THEN** the spinner and elapsed time update without child stdout

#### Scenario: Percent only with a trustworthy total

- **WHEN** streaming dump/filestore bytes provide a total
- **THEN** percent is shown; without a total, only spinner and elapsed time are shown

#### Scenario: Error preserves primary cause and last stage

- **WHEN** a restore fails
- **THEN** the safe primary cause, last stage id, and elapsed seconds are preserved instead of a generic readiness message

#### Scenario: Machine output is unchanged

- **WHEN** a long restore runs in JSON or TOON
- **THEN** the single-document contract and exit code are preserved and terminal progress does not pollute machine stdout

### Requirement: Auxiliary Odoo stdout/stderr are drained concurrently

A spawned long-running auxiliary Odoo handle SHALL be drained by the `command-execution` process pump (`inherit_stdio=False`, 8192-byte redacted tail). This requirement SHALL NOT duplicate that contract.

#### Scenario: noisy auxiliary child does not block

- **WHEN** an auxiliary child writes more than the pipe capacity
- **THEN** it does not block and passes readiness/cleanup

#### Scenario: simultaneous drain with bounded memory

- **WHEN** both `stdout` and `stderr` are active
- **THEN** they are drained simultaneously and the tail memory is bounded

#### Scenario: readiness failure contains primary cause and tail

- **WHEN** readiness/startup fails
- **THEN** the failure contains the primary safe cause and a bounded redacted tail, not a single generic message

#### Scenario: cleanup terminates readers and process group

- **WHEN** cleanup or interrupt runs
- **THEN** the child process group and readers are terminated with no leaked threads or handles

### Requirement: `OdooInstance.run_foreground_command()`

`OdooInstance.run_foreground_command()` SHALL launch the resolved Odoo runtime in the foreground. The convenience `run_foreground()` SHALL only delegate to that captured command. Expression SHALL NOT appear in lock acquire/release, spawn, wait, cleanup, or compensation. The command SHALL enter the shared artifact lock only for the atomic spawn and runtime-identity registration, then release the lock before `wait_foreground_process()`. On exit, it SHALL re-acquire that same shared artifact lock for cleanup/revalidation. The wait SHALL NOT hold the shared artifact lock, so a parallel `stop` can acquire the exclusive lock, read the persisted runtime identity, and call the existing `terminate_pid()`. Foreground stdio, signals, exit code, PID/create-time/process-group validation, and stale-runtime safety SHALL be preserved.

#### Scenario: Foreground run does not hold the lock while waiting

- **WHEN** `run_foreground_command()` is waiting on the foreground process
- **THEN** the shared artifact lock is not held and a parallel `stop` can acquire the exclusive lock

#### Scenario: Live E2E parallel stop

- **WHEN** a live E2E runs real `odcli run` in one CLI session and real `odcli stop` in a second while the first is waiting
- **THEN** `stop` succeeds, the registered process group terminates, the HTTP port is released, and runtime identity is cleaned up

#### Scenario: Parallel stop terminates the foreground runtime

- **WHEN** a parallel `stop` runs while `run_foreground_command()` is waiting
- **THEN** `stop` acquires the exclusive lock, reads the persisted runtime identity, and terminates the registered runtime

#### Scenario: Foreground stdio and exit code are preserved

- **WHEN** `run_foreground_command()` runs normally, exits non-zero, or is interrupted
- **THEN** inherited stdin/stdout/stderr remain native, the real exit code is returned, and interrupt cleanup preserves exit `130`
