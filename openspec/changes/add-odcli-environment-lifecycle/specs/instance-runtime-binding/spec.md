## ADDED Requirements

### Requirement: `InstanceConfig.command_prefix` and `default_cwd`

`InstanceConfig` MUST включать новые поля:

- `command_prefix: tuple[str, ...] | None = None`
- `default_cwd: Path | None = None`

Правила prefix/cwd:

- `from_environment()` записывает recorded `[python, odoo-bin]` и resolved `runtime_cwd`;
- `from_config(path)` записывает `[OdooClientConfig.executable]` если executable set, иначе `None` (fallback на `OdooClientConfig.executable` at run time); не требует master password;
- ручной `instance(base_url=...)` оставляет `command_prefix=None` и падает назад на `OdooClientConfig.executable`.

#### Scenario: from_environment sets prefix

- **WHEN** `InstanceFactory.from_environment(env)` создаёт instance
- **THEN** `command_prefix = [recorded-python, odoo-bin]`, `default_cwd = resolved runtime_cwd`

#### Scenario: from_config sets prefix

- **WHEN** `InstanceFactory.from_config("odoo.conf")` создаёт instance и `OdooClientConfig.executable` is set
- **THEN** `command_prefix = [OdooClientConfig.executable]`

#### Scenario: from_config no executable — None prefix

- **WHEN** `InstanceFactory.from_config("odoo.conf")` и `OdooClientConfig.executable` is None
- **THEN** `command_prefix = None`, runtime falls back to `OdooClientConfig.executable` (which is None → error at run time)

#### Scenario: Manual instance no prefix

- **WHEN** `client.instance("http://localhost:8069")` создаёт instance
- **THEN** `command_prefix = None`, fallback на `OdooClientConfig.executable`

### Requirement: `InstanceFactory.from_environment()`

`InstanceFactory.from_environment(environment: DevelopmentEnvironment) -> OdooInstance` MUST:

- принимать только `ready` environment;
- читать generated `odoo.conf` через существующий config flow;
- применять recorded Python interpreter (shared or owned), Odoo entry point и worktree как defaults для запуска;
- использовать recorded resolved runtime paths, не перечитывая project manifest;
- не требовать master password — `master_password=None`;
- возвращать обычный `OdooInstance`, а не новую runtime wrapper abstraction;
- не переносить Git, cleanup или audit methods в `OdooInstance`.

#### Scenario: from_environment without master password

- **WHEN** `from_environment(env)` для ready environment
- **THEN** `OdooInstance.config.master_password is None`

#### Scenario: from_environment on non-ready environment

- **WHEN** `from_environment(env)` для `state != ready` environment
- **THEN** error (only ready environments accepted)

### Requirement: `from_config()` without mandatory master password

`from_config(path, base_url=None, master_password=None)` MUST NOT поднимать `MasterPasswordRequiredError`, если `admin_passwd` отсутствует: поле `master_password` остаётся `None`.

`MasterPasswordRequiredError` возникает только на mutating DB-методах (`backup`/`restore`/`drop`) в момент call.

#### Scenario: from_config without admin_passwd

- **WHEN** `from_config("odoo.conf")` и `admin_passwd` отсутствует в config
- **THEN** instance создаётся с `master_password=None`; `list()`/`exists()` доступны; `backup()` поднимает `MasterPasswordRequiredError`

#### Scenario: from_config with explicit password

- **WHEN** `from_config("odoo.conf", master_password="secret")`
- **THEN** `master_password="secret"`, mutating DB methods работают

### Requirement: `StartConfig.from_odoo_config(path)` records actual path

`StartConfig.from_odoo_config(path)` MUST устанавливать `config_path` в фактический `path`, а не ждать option `config_path` внутри файла.

`_build_cli_args()` MUST передавать ровно один `--config`.

При persistent generated config с правами `0600` SDK MUST NOT добавлять второй временный config только из-за `db_password`.

(Полные сценарии — см. `models-types` spec, requirement "`StartConfig.from_odoo_config(path)` records actual path".)

#### Scenario: Single --config in argv

- **WHEN** `_build_cli_args()` builds argv для persistent `0600` generated conf
- **THEN** ровно один `--config <path>`, no second temp config from `db_password`

### Requirement: `DatabaseResource.restore()` POST body name

`DatabaseResource.restore()` MUST отправлять `"name": target_database_name` в POST body.

(Полные сценарии — см. `database-restore` spec, requirement "Восстановление базы".)

#### Scenario: Restore sends target name

- **WHEN** `restore(backup, target_database_name="comerta_x")`
- **THEN** POST body содержит `"name": "comerta_x"`

### Requirement: `OdooInstance.run_foreground()`

`OdooInstance.run_foreground(config: StartConfig | None = None, *, cwd=None, env=None) -> int` MUST:

- если `config is None`, использовать `self.config.start_config` (from `from_config()`/`from_environment()`); если `start_config` is None → `InstanceConfigurationError`;
- использовать тот же resolved command-prefix/config/process-group lifecycle, что и `start()`/`stop()`;
- наследовать stdout/stderr, поэтому Odoo logs идут прямо в terminal без буферизации, SQLite-хранения, tail API или собственного форматирования;
- блокироваться до завершения Odoo и возвращать exit code;
- на Ctrl+C корректно остановить owned process group.

#### Scenario: Foreground run with explicit config

- **WHEN** `instance.run_foreground(config=cfg)` and Odoo exits with code 0
- **THEN** returns `0`

#### Scenario: Foreground run uses start_config

- **WHEN** `instance.run_foreground()` (config=None) и instance создан через `from_environment()` со `start_config` from generated `odoo.conf`
- **THEN** uses `self.config.start_config`

#### Scenario: Foreground run no start_config — error

- **WHEN** `instance.run_foreground()` (config=None) и `self.config.start_config is None`
- **THEN** `InstanceConfigurationError`

#### Scenario: Ctrl+C stops process group

- **WHEN** `instance.run_foreground()` получает Ctrl+C
- **THEN** owned process group stopped, CLI exits 130

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

Primitive входит в MVP public instance API, чтобы не плодить второй captured runner позже. В MVP CLI `eval`/`exec` не появляются, но `run_shell_script()` тестируется как SDK method.

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