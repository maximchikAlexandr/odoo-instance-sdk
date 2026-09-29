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

### Requirement: Persisted environment runtime can be stopped safely

The owner-neutral runtime record and its captured launch identity SHALL be the starting point for out-of-process stop. The runtime catalogue SHALL add one nullable JSON field for a versioned launch-identity document. Every new environment- or project-owned foreground or detached launch SHALL populate that field from the exact immutable `PreparedStep` used by execution, before foreground waiting or detached return. The document SHALL contain only JSON-safe, secret-free identity evidence: schema version, canonical executable, the common redacted argv projection with preserved boundaries, captured sensitive-argument positions, executable-prefix length, canonical cwd, and canonical config path. It SHALL NOT contain raw passwords, secret-bearing values, inherited environment values, or a digest of secret material.

The stop path SHALL select the exact `_RuntimeBinding` owner, re-read that owner's runtime row and PID/create time, decode the captured launch identity, and project the live argv through the same common redaction rules and persisted sensitive positions. It SHALL compare PID create time, executable, captured executable prefix, protected option names and projected values, cwd, and config path. POSIX stop SHALL additionally require the live SDK-created process-group identity `pgid == pid` before using the existing bounded terminate-and-kill escalation; Windows SHALL require the same available identity checks before existing process-tree termination. The stop path SHALL NOT rebuild authoritative launch identity from current project manifest, current `StartConfig`, current default run args, generated configuration contents, Git branch, or checkout revision.

Mismatch evidence SHALL be bounded, deterministic, and value-free. An argv mismatch SHALL identify `executable-prefix` or the differing protected option name, such as `--database`; other mismatch labels SHALL remain limited to safe component names such as `create_time`, `executable`, `cwd`, `config`, and `process_group`. A secret-bearing option SHALL retain its option name and redaction marker only; neither persisted state nor exceptions SHALL expose its value. A malformed, unsupported, or absent launch-identity document SHALL fail closed before signaling and retain the runtime row with an actionable sanitized reason.

Successful exit SHALL be verified before an atomic conditional delete of the exact `(owner_kind, owner_id, root_pid, create_time)` row. Project registration SHALL remain intact. The implementation SHALL add no second process registry, supervisor, port-based ownership inference, or owner-specific termination algorithm. `OdooInstance` foreground runtime identity registration and cleanup SHALL remain under the artifact lock, but foreground waiting SHALL happen outside the lock so parallel stop remains possible. Expression SHALL NOT appear in lock acquire/release, registration, wait, or cleanup.

#### Scenario: Environment-owned captured identity matches

- **WHEN** an environment-owned runtime row contains the identity captured from its immutable launch step and every required live-process identity check matches that snapshot
- **THEN** the existing process boundary terminates only that process tree, verifies absence, and clears only that environment-owned runtime row

#### Scenario: Project checkout evolves after launch

- **WHEN** a project-owned runtime remains unchanged while the checkout branch, revision, manifest-derived defaults, or current configuration source changes after launch
- **THEN** stop validates against the persisted launch identity, terminates the owned process, verifies exit, clears only its project runtime row, and preserves project registration

#### Scenario: Protected live binding differs

- **WHEN** the live process differs from the captured launch identity in an executable-prefix element or a non-secret protected binding such as `--database`, `--config`, `--addons-path`, or `--http-port`
- **THEN** no signal is sent, the runtime row is retained, and the error names only `argv: executable-prefix` or the differing option name without either value

#### Scenario: Secret-bearing identity remains redacted

- **WHEN** launch argv contains a secret-bearing protected option
- **THEN** the persisted document, plan, fingerprint, representation, and mismatch diagnostic use the common redaction marker and never contain or digest the raw secret

#### Scenario: PID was reused or process identity changed

- **WHEN** the PID exists but its create time, executable, captured argv evidence, cwd, config argument, or required POSIX process group differs, or required identity evidence is inaccessible
- **THEN** no signal is sent and the stale row is retained for actionable diagnosis

#### Scenario: Legacy runtime lacks captured identity

- **WHEN** a migrated pre-change runtime row has no captured launch-identity document
- **THEN** stop fails closed with a sanitized `captured launch identity unavailable` reason, sends no signal, retains the row, and does not reconstruct authority from mutable current configuration

#### Scenario: Process exited before execution

- **WHEN** planning observed a matching persisted owner but execution revalidation proves that PID absent for either owner kind
- **THEN** stop returns idempotent success and conditionally clears only the now-stale matching owner row without signaling another process

#### Scenario: Runtime row changed before cleanup

- **WHEN** the matching owner's PID, create time, or captured launch identity changes after planning but before execution or cleanup
- **THEN** stop fails closed without signaling or deleting the replacement runtime row

#### Scenario: Foreground wait does not block stop

- **WHEN** a foreground `run` is waiting on the foreground process
- **THEN** the artifact lock is not held and a parallel stop acquires the exclusive lock and validates the captured identity before termination

### Requirement: Effective runtime diagnosis
Doctor SHALL reuse the existing resolver/runtime view to report owner kind (`environment` or `project`), selection source, and effective Python, Odoo binary, config, database, and HTTP URL while distinguishing configured values from current availability. It SHALL work for initialized default checkouts, start no process, and expose no passwords or environment secrets. [Source: GH#43]

#### Scenario: Diagnose project without environment
- **WHEN** doctor runs in an initialized default checkout with invalid Python, stopped Odoo, available PostgreSQL, or ambiguous database state
- **THEN** it reports each configured/available fact and actionable finding under project ownership without inventing an environment

### Requirement: Canonical worktree path is the single source

All execution plans, runtime/session ownership, provenance, and configuration SHALL resolve environment worktree paths from the canonical `~/.odcli` root. The legacy `~/Library/Application Support/odoo-instance-sdk` path SHALL be used only by the one-time migration/compatibility code. After migration, no production read, write, command construction, runtime identity, cleanup, or documentation SHALL depend on the legacy path or its compatibility symlink. The repository and tests SHALL verify the legacy path is not used outside the bounded migration/compatibility code.

#### Scenario: Run uses canonical worktree path

- **WHEN** `odcli --project /path --env UUID run --dry-run` builds a plan
- **THEN** `cwd`, `--addons-path`, and Git provenance commands reference the canonical `~/.odcli` worktree path

#### Scenario: Run works without legacy symlink

- **WHEN** the compatibility symlink and legacy directory are removed
- **THEN** `run` and `run --dry-run` still work for a registered environment

#### Scenario: Runtime ownership is consistent across commands

- **WHEN** a registered environment's Odoo is running
- **THEN** `env list`, `run --dry-run`, port preflight, and active-session lookup all recognize the same runtime and owner
