## MODIFIED Requirements

### Requirement: `OdooInstance.run_foreground()`

`OdooInstance.run_foreground(config: StartConfig | None = None, *, cwd=None, env=None) -> int` MUST:

- если `config is None`, использовать `self.config.start_config` (from `from_config()`/`from_environment()`); если `start_config` is None → `InstanceConfigurationError`;
- использовать тот же resolved command-prefix/config/process-group lifecycle, что и `start()`/`stop()`;
- наследовать stdout/stderr, поэтому live Odoo output идёт прямо в terminal без буферизации, SQLite-хранения или собственного форматирования; tail native logfile — отдельный `iter_logs()`, не этот метод;
- блокироваться до завершения Odoo и возвращать exit code;
- на Ctrl+C корректно остановить owned process group.

Дополнительно (новое в этом change): после spawn Odoo process и до блокировки `run_foreground()` MUST persist current runtime identity в catalog `environment_runtime` (если instance bound к environment через `from_environment()`; manual instance через `instance(base_url=...)`/`from_config()` — без environment_id, runtime identity НЕ пишется):

- `root_pid` — Odoo root PID;
- `create_time` — `psutil.Process(root_pid).create_time()` (если `psutil` доступен) или `time.time()` fallback;
- `started_at` — tz-aware UTC ISO now;
- `checkout_branch` и `commit_sha` — из environment record (`git rev-parse --abbrev-ref HEAD` + `git rev-parse HEAD` в worktree);
- `http_url`/`http_port` — `http_url = f"http://{StartConfig.http_interface}:{StartConfig.http_port}"` (полный URL, UI "Open Odoo" открывает его); `http_port` из `StartConfig.http_port`;
- `database_name` — `db_name` из `StartConfig` (target DB для copy, source DB для shared).

В `finally` (normal exit, Odoo crash, Ctrl+C/exit 130, любой exception) `run_foreground()` MUST очистить runtime identity (`clear_environment_runtime(environment_id)`), так чтобы stopped environment не оставляла stale PID. Очистка best-effort: если catalog уже закрыт/ошибся, ошибка логируется в stderr и не маскирует original exit code/exception.

`psutil` import ленивый: если extra `metrics` не установлен, `create_time` falls back to `time.time()`; `run_foreground()` сам не требует `psutil` (only collector does). `run_foreground()` не запускает `EnvironmentMonitor` и не собирает child PIDs/CPU/RAM — он только persist identity; live metrics собирает collector отдельно.

`shell()`, `run_shell_script()`, `start()` и `stop()` — **без изменения семантики**. `shell()`/`run_shell_script()` — short-lived captured/interactive commands; они НЕ persist runtime identity (только `run_foreground` — длительный foreground server). `start()` возвращает `OdooProcess` и регистрирует в in-memory `OdooClient` registry (не в catalog) — это не меняется. `stop()` — только in-memory registry.

#### Scenario: Foreground run with explicit config

- **WHEN** `instance.run_foreground(config=cfg)` and Odoo exits with code 0
- **THEN** returns `0`, runtime identity is cleared in catalog `finally`

#### Scenario: Foreground run uses start_config

- **WHEN** `instance.run_foreground()` (config=None) и instance создан через `from_environment()` со `start_config` from generated `odoo.conf`
- **THEN** uses `self.config.start_config`, runtime identity persisted after spawn

#### Scenario: Foreground run no start_config — error

- **WHEN** `instance.run_foreground()` (config=None) и `self.config.start_config is None`
- **THEN** `InstanceConfigurationError`

#### Scenario: Ctrl+C stops process group

- **WHEN** `instance.run_foreground()` получает Ctrl+C
- **THEN** owned process group stopped, exit code 130

#### Scenario: Ctrl+C clears runtime identity in finally

- **WHEN** `instance.run_foreground()` получает Ctrl+C
- **THEN** runtime identity cleared in `finally` before CLI exits 130

#### Scenario: Manual instance does not persist runtime identity

- **WHEN** `client.instance("http://localhost:8069").run_foreground()` (no environment_id)
- **THEN** no `environment_runtime` row is written or cleared

#### Scenario: create_time from psutil when available

- **WHEN** `psutil` is installed and `run_foreground()` spawns Odoo PID 43120
- **THEN** `environment_runtime.create_time == psutil.Process(43120).create_time()`

#### Scenario: create_time falls back without psutil

- **WHEN** `psutil` is not installed and `run_foreground()` spawns Odoo
- **THEN** `environment_runtime.create_time == time.time()` (approx); `run_foreground()` itself does not require `psutil`

#### Scenario: Crash still clears runtime identity

- **WHEN** Odoo crashes mid-foreground with non-zero exit
- **THEN** `finally` clears the `environment_runtime` row before `run_foreground()` returns

#### Scenario: Shell does not persist runtime identity

- **WHEN** `instance.shell()` executes
- **THEN** no `environment_runtime` row is written (only `run_foreground` persists identity)