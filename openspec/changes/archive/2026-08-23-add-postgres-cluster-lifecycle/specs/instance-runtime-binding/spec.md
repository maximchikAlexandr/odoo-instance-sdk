## MODIFIED Requirements

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

## ADDED Requirements

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
