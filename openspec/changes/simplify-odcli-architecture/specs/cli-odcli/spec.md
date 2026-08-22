## ADDED Requirements

### Requirement: Root CLI module is registration only

Entry point MUST оставаться:

```toml
[project.scripts]
odcli = "odoo_instance_sdk.cli:cli"
```

Корневой модуль `odoo_instance_sdk.cli` MUST содержать только Click group registration, global options `--project` / `--env` и подключение command groups. Он MUST NOT содержать реализации `init`, `env *`, `run`, `shell`, `doctor`, `eval`, `exec`, `module`, `translations`, `deps`, `vscode` и MUST NOT содержать JSON/human serialization, catalog writes или runtime verification.

Command groups MUST жить в focused internal modules. Имена команд, options, help surface и import `from odoo_instance_sdk.cli import cli` MUST сохраниться.

#### Scenario: Help still lists full command surface

- **WHEN** `odcli --help` runs after the split
- **THEN** shows init, env, run, shell, doctor, eval, exec, module, translations, deps, vscode

#### Scenario: Entry point import is unchanged

- **WHEN** Python imports `odoo_instance_sdk.cli.cli`
- **THEN** the Click group is available and `[project.scripts] odcli` still points at `odoo_instance_sdk.cli:cli`

#### Scenario: Root module has no command bodies

- **WHEN** the root CLI module is inspected
- **THEN** it has no command implementations, no JSON/human serialization and no catalog writes; those live in internal modules

### Requirement: One typed application context

Повторяющиеся project/environment resolution, runtime verification и instance construction MUST идти через один typed internal application context, а не через разрозненные helpers в command modules.

Application context MUST:

- принимать raw `--project` / `--env` и cwd;
- резолвить project и environment по already-specified two-rule context;
- создавать один `OdooClient` на invocation;
- проверять ready state, worktree/config/Python и port preflight для instance commands, которые этого требуют;
- строить `OdooInstance` через `from_environment()`;
- отдавать envelope `provenance` (`project_source` / `environment_source` или init option/vscode/discovery/default) отдельно от `context`.

Command modules MUST NOT создавать `OdooClient` сами и MUST NOT дублировать resolve/verify/from_environment.

Silent last-used и silent single-ready selection остаются запрещены.

#### Scenario: Instance command uses one context

- **WHEN** `odcli run` executes inside a registered worktree
- **THEN** project, environment, runtime checks and `from_environment()` go through the same typed context object

#### Scenario: Command module does not construct its own client

- **WHEN** `odcli env list --json` executes
- **THEN** the env command obtains `OdooClient` from application context, not by constructing a client locally

### Requirement: CLI rendering does not mutate lifecycle persistence

JSON envelope builders, human table/text printers и error emitters MUST NOT вызывать catalog writes и MUST NOT обновлять `last_used_at` или environment events.

Lifecycle persistence (`last_used_at`, `use/succeeded` и прочие environment events) MUST оставаться за environment/application boundary. Output helpers MAY только печатать уже вычисленные значения.

`run` по-прежнему MUST обновлять `last_used_at` и писать `use/succeeded` после free-port preflight и MUST NOT делать это при `port-conflict`. Writer — application/environment boundary, не output rendering.

#### Scenario: JSON success path does not write catalog

- **WHEN** `odcli env list --json` prints the envelope
- **THEN** catalog `environments.last_used_at` and `environment_events` are unchanged by the renderer

#### Scenario: Port conflict still skips use persistence

- **WHEN** `odcli run` hits occupied `http_interface:http_port`
- **THEN** output is deterministic `port-conflict` / ownership-unknown and no `last_used_at` / `use` event is written

#### Scenario: Successful run still records use

- **WHEN** `odcli run` finds a free port and starts the instance
- **THEN** `last_used_at` and `use/succeeded` are recorded before `run_foreground()`, not from the JSON/human printer

### Requirement: Single success and error serialization path

Bounded leaf commands MUST эмитить human text или ровно один versioned JSON envelope через один shared output path. Duplicate envelope/error helpers с тем же контрактом MUST NOT существовать.

Shared output path MUST сохранять текущий v1 envelope целиком: `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, `warnings`; на success — оба ключа `result` и `data` с одинаковым payload; на error — `error.code` и sanitized `error.message`. Output path MUST NOT отбрасывать `provenance` / `dry_run` и MUST NOT делать `result` optional.

#### Scenario: Env list JSON uses the shared envelope

- **WHEN** `odcli env list --json` succeeds
- **THEN** stdout is exactly one schema_version=1 envelope with `command` `env.list` and both `result` and `data`

#### Scenario: Init failure and env failure share error shape

- **WHEN** `odcli init --no-input --json` misses `--odoo-bin` and `odcli env checkout missing --json` fails
- **THEN** both envelopes use the same wrapper keys including `provenance` and `dry_run`, plus `error.code` and sanitized `error.message`