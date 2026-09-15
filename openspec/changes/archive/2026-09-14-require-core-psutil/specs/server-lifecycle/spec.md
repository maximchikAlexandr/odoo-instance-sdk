## MODIFIED Requirements

### Requirement: `OdooInstance.run_foreground()`

`OdooInstance.run_foreground(config: StartConfig | None = None, *, cwd=None, env=None) -> int` MUST:

- если `config is None`, использовать `self.config.start_config` (from
  `from_config()`/`from_environment()`); если `start_config` is None →
  `InstanceConfigurationError`;
- использовать тот же resolved command-prefix/config/process-group lifecycle,
  что и `start()`/`stop()`;
- наследовать stdout/stderr, поэтому live Odoo output идёт прямо в terminal без
  буферизации, SQLite-хранения или собственного форматирования; tail native
  logfile — отдельный `iter_logs()`, не этот метод;
- блокироваться до завершения Odoo и возвращать exit code;
- на Ctrl+C корректно остановить owned process group.

После spawn (только для instance bound к environment через
`from_environment()`) `run_foreground()` MUST persist current runtime identity
in catalog `environment_runtime` using the exact
`psutil.Process(root_pid).create_time()` value for `create_time`, plus
`root_pid`, `started_at`, branch/commit, `http_url`/`http_port`, and
`database_name`. It MUST NOT persist an approximate wall-clock substitute.
In `finally` it MUST clear runtime identity best-effort. If the foreground wait
raises unexpectedly, it MUST terminate and reap the owned process group before
clearing the identity and re-raising, even when the leader has already exited
but descendants remain. Any cleanup or catalog-clear error is best-effort and
MUST NOT mask the original persistence or wait exception; a persistence failure
after spawn is fail-closed and terminates the owned group before re-raising.
Manual instance — без persist. `shell()`/`run_shell_script()`/`start()`/`stop()`
— без persist (только `run_foreground`).

#### Scenario: Foreground run with explicit config

- **WHEN** `instance.run_foreground(config=cfg)` and Odoo exits with code 0
- **THEN** returns `0`, runtime identity is cleared in catalog `finally`

#### Scenario: Foreground identity is exact

- **WHEN** an environment-bound foreground process is spawned
- **THEN** its catalog `create_time` equals that process's exact
  `psutil.Process(pid).create_time()` value

#### Scenario: Foreground run uses start_config

- **WHEN** `instance.run_foreground()` (config=None) и instance создан через
  `from_environment()` со `start_config` from generated `odoo.conf`
- **THEN** uses `self.config.start_config`

#### Scenario: Foreground run no start_config — error

- **WHEN** `instance.run_foreground()` (config=None) и `self.config.start_config is None`
- **THEN** `InstanceConfigurationError`

#### Scenario: Ctrl+C stops process group

- **WHEN** `instance.run_foreground()` получает Ctrl+C
- **THEN** owned process group stopped, runtime identity cleared in `finally`, CLI exits 130

#### Scenario: Unexpected wait failure reaps owned process

- **WHEN** the foreground wait raises after the leader exits but an owned
  descendant remains live
- **THEN** the group is terminated and reaped, runtime identity is cleared
  best-effort, and the original exception is re-raised

#### Scenario: Manual instance does not persist runtime identity

- **WHEN** `client.instance("http://localhost:8069").run_foreground()` (no environment_id)
- **THEN** no `environment_runtime` row is written or cleared

#### Scenario: Shell does not persist runtime identity

- **WHEN** `instance.shell()` executes
- **THEN** no `environment_runtime` row is written (only `run_foreground` persists identity)
