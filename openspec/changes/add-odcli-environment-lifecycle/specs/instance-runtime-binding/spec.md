## ADDED Requirements

### Requirement: `InstanceConfig.command_prefix` and `default_cwd`

`InstanceConfig` MUST включать новые поля:

- `command_prefix: tuple[str, ...] | None = None`
- `default_cwd: Path | None = None`

Правила prefix/cwd:

- `from_environment()` записывает recorded `[python, odoo-bin]` и resolved `runtime_cwd`;
- `from_config(path)` и `instance(base_url=...)` оставляют `command_prefix=None`; runtime fallback — `OdooClientConfig.executable`. Config не выдумывает Python interpreter.

#### Scenario: from_environment sets prefix

- **WHEN** `InstanceFactory.from_environment(env)` создаёт instance
- **THEN** `command_prefix = [recorded-python, odoo-bin]`, `default_cwd = resolved runtime_cwd`

#### Scenario: from_config no python prefix

- **WHEN** `InstanceFactory.from_config("odoo.conf")` создаёт instance
- **THEN** `command_prefix is None`, runtime falls back to `OdooClientConfig.executable`

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


`StartConfig.from_odoo_config(path)` и single `--config` живут в `models-types`. `restore()` POST `name` — в `database-restore`. `run_foreground`/`shell`/`run_shell_script` — в `server-lifecycle`.
