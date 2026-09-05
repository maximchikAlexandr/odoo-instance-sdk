## Purpose

Binding an ordinary OdooInstance to a ready development environment through recorded command prefix and cwd, without a second runtime wrapper.
## Requirements
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
- не переносить Git, cleanup или audit methods в `OdooInstance`;
- additionally bind the project cluster to resulting `OdooInstance` через internal field `_postgres_cluster: PostgresCluster | None`;
- cluster bind происходит через `PostgresCluster.from_project(Path(environment.repository_root))`;
- bind не запускает cluster и не проверяет readiness (это preflight перед spawn);
- bind не падает если cluster не ready (только явный `ensure_running` в preflight);
- возвращать обычный `OdooInstance` (не новый wrapper abstraction).

#### Scenario: from_environment binds cluster

- **WHEN** `InstanceFactory.from_environment(env)` on a project with `[postgres] mode="compose"`
- **THEN** resulting `OdooInstance` has `_postgres_cluster` set, cluster is not started

#### Scenario: from_environment legacy project

- **WHEN** `InstanceFactory.from_environment(env)` on a project without `[postgres]`
- **THEN** `_postgres_cluster` is set to an external-mode cluster (bind still happens, preflight probes reachability)

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

### Requirement: `OdooInstance` dependency preflight before spawn

`OdooInstance.run_foreground()`, `shell()` и `run_shell_script()` (включая `_run_shell_script_exclusive`) MUST вызывать ровно один internal dependency preflight до spawning Odoo process (до acquire artifact lock).

Preflight MUST delegate cluster readiness в `PostgresCluster.ensure_running()` если `_postgres_cluster` не `None`. CLI MUST NOT дублировать preflight.

Когда managed cluster остановлен, любой Odoo process command (run/shell/script) запускает его сначала через preflight. Когда Odoo exits, project cluster остаётся running (`run_foreground` не вызывает `stop`) — другие environments могут его использовать.

Удаление одного environment (`EnvironmentResource.remove`) MUST NEVER останавливать или удалять shared project cluster.

`OdooInstance` с `_postgres_cluster=None` (manual instance через `instance(base_url=...)` или `from_config()`) MUST NOT выполнять preflight (no-op).

#### Scenario: Preflight runs before foreground spawn

- **WHEN** `instance.run_foreground()` on an instance bound to a stopped compose cluster
- **THEN** `PostgresCluster.ensure_running()` is invoked before Odoo process spawns, cluster becomes healthy, then Odoo starts

#### Scenario: Preflight runs once per call

- **WHEN** `instance.run_shell_script(source)` executes
- **THEN** `ensure_running()` is called exactly once before the shell subprocess

#### Scenario: Preflight not duplicated by CLI

- **WHEN** `odcli run` invokes `instance.run_foreground()`
- **THEN** CLI does not call `ensure_running()` separately; only `OdooInstance` preflight runs

#### Scenario: Cluster stays running after Odoo exits

- **WHEN** `instance.run_foreground()` returns after Odoo exits
- **THEN** project cluster remains running (no `stop()` called by `run_foreground`)

#### Scenario: Manual instance has no preflight

- **WHEN** `client.instance("http://localhost:8069").run_foreground()`
- **THEN** no cluster preflight (no `_postgres_cluster`), existing `InstanceConfigurationError` if no `start_config`

#### Scenario: Environment removal does not stop cluster

- **WHEN** `client.environments.remove(env)` on a compose-mode project
- **THEN** project cluster is not stopped or removed; only environment artifacts (worktree, venv, config) are cleaned

### Requirement: Runtime ownership is environment or project

A foreground instance constructed from a ready environment SHALL persist runtime identity with `environment_id`; one constructed from an initialized project SHALL persist the same identity with `project_id` and no environment ID. Exactly one owner kind SHALL be present. Project ownership SHALL use canonical repository/project identity already recorded by project initialization and SHALL NOT synthesize an environment row. Manual instances SHALL remain unpersisted.

#### Scenario: Project foreground runtime is recorded
- **WHEN** a project-bound foreground Odoo process starts successfully
- **THEN** its PID, create time, start time, revision, URL, port, database, and project owner are persisted without an environment owner

#### Scenario: Ownership is exclusive
- **WHEN** any persisted runtime row is validated
- **THEN** exactly one of environment owner or project owner is present

### Requirement: Project runtime cleanup preserves stale-process safety

Project-owned runtime identity SHALL be cleared best-effort in the same foreground `finally` path as environment-owned identity. Readers SHALL validate PID create time and other existing identity checks before treating either owner kind as live.

#### Scenario: Project runtime exits
- **WHEN** a project-owned foreground process exits normally, fails, or is interrupted
- **THEN** its runtime identity is cleared without deleting project registration

### Requirement: Project registration writes are explicit

Canonical project registration SHALL be written only after successful non-preview project initialization and immediately before an allowed normal foreground execution/lifecycle runtime write. Project resolution by itself, read-only commands, monitor collection, failed init, and every dry-run SHALL perform no catalogue mutation. The foreground write point SHALL provide the compatibility path for an already initialized legacy project-only checkout with no environment rows; it SHALL upsert the project registration without creating an environment or environment lifecycle event.

#### Scenario: Preview is inert
- **WHEN** an unregistered legacy initialized project runs `odcli run --dry-run` or another preview/read-only operation
- **THEN** its plan is returned and no project, runtime, environment, or lifecycle catalogue row is written

#### Scenario: Normal legacy run registers project
- **WHEN** the same checkout starts an allowed normal foreground run
- **THEN** canonical project registration is upserted before runtime persistence and monitor discovery can include it without an environment row
