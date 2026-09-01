## Purpose

Click CLI adapter over the SDK for project init, environment lifecycle, diagnostics, and local Odoo automation.
## Requirements
### Requirement: Click entry point

SDK MUST добавлять один Click entry point:

```toml
[project.scripts]
odcli = "odoo_instance_sdk.cli:cli"
```

CLI — тонкий adapter над SDK, не оркестратор процессов.

Help и synopsis MUST показывать полный command surface:

```text
odcli [--project PATH] COMMAND
odcli [--project PATH] [--env SELECTOR] <instance-command>

odcli init [OPTIONS]
odcli env checkout BRANCH [OPTIONS]
odcli env sync [ENVIRONMENT] [OPTIONS]
odcli env list [OPTIONS]
odcli env remove [ENVIRONMENT] [OPTIONS]
odcli run
odcli logs [-n|--tail N] [-f|--follow]
odcli shell [-- ODOO_ARGS...]
odcli doctor [OPTIONS]
odcli monitor [--headless] [--host HOST] [--port PORT] [--no-open]
odcli eval EXPRESSION [OPTIONS]
odcli exec SCRIPT [-- SCRIPT_ARGS...]
odcli test [TARGET] [OPTIONS]
odcli module list [MODULE...] [OPTIONS]
odcli module update MODULE... [OPTIONS]
odcli module test MODULE... [OPTIONS]
odcli translations export --module MODULE... [OPTIONS]
odcli deps verify [OPTIONS]
odcli vscode generate [OPTIONS]
```

#### Scenario: Help shows full command surface

- **WHEN** `odcli --help` runs
- **THEN** shows init, env, run, logs, shell, doctor, monitor, eval, exec, test, module, translations, deps, vscode

### Requirement: CLI not a third runtime

`odcli` MUST NOT: стартовать/останавливать/регистрировать процессы сам, держать process table, писать generated config в обход `EnvironmentResource`, реализовывать Git/`uv`/lock API, угадывать environment по recency или по «единственному ready».

`odcli` MAY: резолвить project/environment по двум правилам, печатать human text или один JSON envelope, вызывать `EnvironmentResource` и `OdooInstance`. CLI MUST NOT acquire flock.

#### Scenario: No process table in CLI

- **WHEN** `odcli run` executes
- **THEN** CLI calls `from_environment()` + `run_foreground()`, does not register/manage process itself

### Requirement: Context-aware command resolution

The following resolution rules MUST apply.

Два правила:

1. Если current directory находится внутри exact registered worktree, project и environment выводятся из этой записи.
2. Иначе нужны явные флаги: `--project PATH` для project commands; `--env SELECTOR` или positional `ENVIRONMENT` по типу команды. Ближайший `.odcli/project.toml` вверх до Git/filesystem boundary считается explicit project discovery, не угадыванием environment.

Запрещено: выбирать «последний использованный» environment; молча брать единственный `ready` environment проекта, если cwd не является его worktree; резолвить global default project.

#### Scenario: Inside registered worktree

- **WHEN** `odcli run` executed inside exact registered worktree
- **THEN** project + environment inferred from worktree record

#### Scenario: Outside worktree without flags

- **WHEN** `odcli run` executed outside worktree without `--env`
- **THEN** error: either `cd` в worktree, либо `--env`, со списком candidates

#### Scenario: Single ready not silently selected

- **WHEN** project has exactly one `ready` environment, cwd not in its worktree, no `--env`
- **THEN** error, never silently select

### Requirement: Project resolution order

Project resolution MUST follow this order.

1. Explicit global `--project PATH` (любой путь внутри project).
2. Ближайший `.odcli/project.toml` от current directory вверх до Git/filesystem boundary.
3. Exact registered worktree containing current directory, resolved через canonical Git common dir.
4. Иначе — ошибка с подсказкой `odcli init` или `--project`.

#### Scenario: Explicit --project

- **WHEN** `odcli --project /path/to/repo env list`
- **THEN** project resolved from explicit flag

#### Scenario: Nearest project.toml

- **WHEN** `odcli env list` in subdir of repo with `.odcli/project.toml`
- **THEN** project resolved from nearest manifest upward

### Requirement: Environment resolution for instance commands

Для instance commands (`run`, `logs`, `shell`, `eval`, `exec`, `test`, `module`, `translations`, `deps verify`):

1. Explicit root `--env SELECTOR` — UUID либо однозначное имя; option допустим только для instance commands.
2. Exact registered worktree containing current directory.
3. Иначе — ошибка: либо `cd` в worktree, либо `--env`, со списком candidates если их несколько.

Никогда не выбирать единственный `ready` молча и никогда не выбирать по recency. Test target/cwd/addon resolution begins only after this environment is resolved and SHALL NOT select a different environment.

#### Scenario: Explicit --env

- **WHEN** `odcli --env <uuid> test sale` runs
- **THEN** environment is resolved from the explicit selector before addon selection

#### Scenario: Ambiguous name

- **WHEN** `odcli --env "feat" test sale` matches 2 environments
- **THEN** error with candidate list and no addon/Git/preflight/Odoo work

### Requirement: Command-specific context rules

Command-specific context handling MUST follow these rules.

- `env checkout BRANCH` и `doctor` требуют project context;
- `env list` вне project context эквивалентен `env list --all-projects` и читает durable global registry; `--all` означает include removed;
- lifecycle `env sync/remove [ENVIRONMENT]` используют positional selector; без него команда разрешена только из exact registered worktree. Root `--env` с lifecycle command — usage error;
- root context options `--project`/`--env` должны появляться в resolved plan/JSON provenance как `explicit` или `cwd`; поле `defaulted` для environment не используется.

#### Scenario: --env with lifecycle command

- **WHEN** `odcli --env <uuid> env remove`
- **THEN** usage error; lifecycle commands use positional selector

#### Scenario: env sync from worktree without positional

- **WHEN** `odcli env sync` inside exact registered worktree (no positional ENVIRONMENT)
- **THEN** command allowed; environment inferred from worktree

#### Scenario: env list --all-projects from anywhere

- **WHEN** `odcli env list --all-projects` executed outside any project
- **THEN** reads durable global registry, no project context required

### Requirement: Stable machine output

The exact bounded structured leaf inventory for normal execution SHALL be the canonical `tests/unit/test_cli_output_modes.py::PUBLIC_LEAF_CASES` data, currently: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `db refresh`, `db reset-admin-password`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. The spec and its tests SHALL derive/verify this one canonical inventory rather than maintain a second table. Each bounded normal-execution leaf SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Existing `--json` SHALL remain a backward-compatible alias for `--format json`. Supplying `--json` with `--format toon` or `--format rich` SHALL be a Click usage error with exit code `2`; supplying `--json --format json` SHALL be accepted. During normal execution, `run`, interactive `shell`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit a document or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; dry-run suppresses native execution and emits one bounded plan document in Rich, JSON, or TOON, with `--json` equivalent to `--format json`.

The CLI SHALL define one CLI-only `OutputMode` with values `rich`, `json`, and `toon`. The mode and envelope types SHALL NOT become public SDK models or FastAPI response models. Each successful or failed bounded operation, including bounded dry-run for an otherwise native-stream command, SHALL first build one JSON-safe CLI envelope v1 containing `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, and `warnings`; success SHALL contain equal `result` and `data`, and failure SHALL contain stable `error.code` and sanitized `error.message`.

JSON and TOON SHALL serialize that exact envelope without building format-specific result graphs. Decoding a TOON document with the selected strict decoder SHALL yield the same JSON value as decoding JSON output for the same operation. Machine modes SHALL emit exactly one UTF-8 document to stdout with no ANSI, prompt, status, progress, or external log text; diagnostics SHALL go to stderr. Renderer selection SHALL NOT change operation execution, exception mapping, or exit code. Native Click parse failures that occur before output-mode resolution SHALL retain Click's stderr usage output and exit code `2`.

For `env remove`, JSON and TOON document modes (including the `--json` alias) SHALL never call `click.confirm`. Without `--yes`, they SHALL NOT execute removal and SHALL emit exactly one sanitized failure envelope with `error.code="confirmation_required"` and exit code `1`. With `--yes`, JSON and TOON SHALL execute the same removal operation and normal success/failure mapping. Interactive Rich mode SHALL retain its existing confirmation behavior.

Rich renderers SHALL remain adjacent to the concrete commands whose typed results they render. They MAY use `Table`, `Status`, `Progress`, and `Live` only when appropriate to the operation; they SHALL NOT introduce a generic renderer interface, registry, or DSL.

#### Scenario: Native command dry-run supports every bounded format

- **WHEN** `odcli run --dry-run` or spawning `odcli shell --dry-run` is requested with `--format rich|json|toon` or `--json`
- **THEN** output contains exactly one bounded plan with `dry_run=true` in the selected format
- **AND** `--json` and `--format json` produce equivalent JSON documents
- **AND** no native child stream starts

#### Scenario: Normal native command stays raw

- **WHEN** `odcli run` or interactive `odcli shell` executes without `--dry-run`
- **THEN** its inherited stream is not wrapped in a bounded document or Rich live view

#### Scenario: Normal native command rejects machine options

- **WHEN** `odcli run` or spawning `odcli shell` is invoked with `--format` or `--json` but without `--dry-run`
- **THEN** Click exits `2` before invoking SDK code or starting a process

#### Scenario: Canonical bounded inventory remains single-source

- **WHEN** the stable machine-output characterization gate compares the documented normal-execution leaves
- **THEN** they equal canonical `PUBLIC_LEAF_CASES`, including `test`, `db refresh`, and `db reset-admin-password`
- **AND** no second bounded-leaf table is introduced

#### Scenario: JSON envelope

- **WHEN** `odcli env list --json` executes
- **THEN** stdout contains exactly one versioned envelope and no progress or log text

#### Scenario: JSON alias preserves envelope v1

- **WHEN** `odcli env list --json` and `odcli env list --format json` run against the same frozen result
- **THEN** each stdout document decodes to the same envelope v1 and contains no ANSI or diagnostic text

#### Scenario: TOON is semantically equal to JSON

- **WHEN** a bounded command succeeds or fails once and its envelope is emitted as JSON and TOON
- **THEN** strict TOON decoding and JSON decoding produce equal Python builtins including `result`/`data`, context, provenance, warnings, and error fields

#### Scenario: Conflicting alias is usage error

- **WHEN** a caller supplies `--json --format toon`
- **THEN** Click exits `2`, does not execute the operation, and does not emit a partial machine document

#### Scenario: Machine diagnostics stay on stderr

- **WHEN** a bounded machine-mode operation reports a sanitized diagnostic in addition to its result
- **THEN** stdout contains exactly one JSON or TOON envelope and the diagnostic is written only to stderr

#### Scenario: Machine remove requires explicit confirmation

- **WHEN** `odcli env remove ENV --format json`, `--format toon`, or `--json` is invoked without `--yes`
- **THEN** no prompt is rendered, removal is not called, stdout contains one failure envelope with `error.code="confirmation_required"`, and the command exits `1`

#### Scenario: Explicit machine remove executes

- **WHEN** `odcli env remove ENV --yes --format json`, `--format toon`, or `--json` is invoked
- **THEN** the same removal operation runs once and its result is emitted as one document under the normal renderer-independent exit mapping

#### Scenario: Secrets redacted

- **WHEN** error occurs during checkout
- **THEN** every machine or Rich error message redacts passwords, config body, and environment variables before emission

### Requirement: Exit codes

CLI commands MUST use the following exit codes.

Exit codes:

- `0` — success;
- `1` — failure;
- `2` — Click usage error;
- `130` — interrupt (Ctrl+C).

Raw run/shell передают Odoo streams как есть.

#### Scenario: Success exit 0

- **WHEN** `odcli env list` succeeds
- **THEN** exit code 0

#### Scenario: Usage error exit 2

- **WHEN** `odcli env checkout` без branch argument
- **THEN** exit code 2 (Click usage)

#### Scenario: Ctrl+C exit 130

- **WHEN** `odcli run` interrupted by Ctrl+C
- **THEN** foreground process group stopped, exit code 130

### Requirement: `odcli run`

```bash
odcli run
odcli --env <environment-id> run
```

Алгоритм:

1. Разрешить ready environment и проверить worktree/config, recorded Python и Odoo entry point. MUST NOT вызывать `sync_python`.
2. Проверить порт через stdlib `socket.bind((http_interface, http_port))`, а при занятом port выполнить только observational HTTP health check для диагностики.
3. Если port занят — независимо от HTTP ответа вернуть deterministic `port-conflict`/ownership-unknown и не менять generated config, не обновлять `used`, не запускать второй process.
4. Обновить `last_used_at` и generic `use/succeeded` event после free-port preflight.
5. Построить instance через `client.instance.from_environment(environment)`.
6. Передать управление `instance.run_foreground()` и вернуть его exit code.
7. На Ctrl+C `run_foreground()` останавливает только process group, созданную этим foreground call, и CLI завершается кодом 130.

Environment resource не запускает, не останавливает и не регистрирует процессы.

#### Scenario: Port conflict deterministic error

- **WHEN** `odcli run` и `socket.bind((http_interface, http_port))` fails (port занят)
- **THEN** deterministic `port-conflict`/ownership-unknown, no second process, no config change, no `used` update

#### Scenario: Free port starts Odoo

- **WHEN** `odcli run` и port свободен
- **THEN** `last_used_at` + `use/succeeded` event, `from_environment()` → `run_foreground()` → exit code

### Requirement: `odcli logs`

```bash
odcli logs
odcli logs --tail 50
odcli logs --follow
odcli --project PATH --env SELECTOR logs --follow
```

`odcli logs` MUST:

- резолвить ready environment тем же `ready_instance` path, что `run`/`shell`;
- вызывать `instance.iter_logs(tail=N, follow=F)` и писать raw log text на stdout;
- принимать `-n, --tail INTEGER` (default 100, MUST быть `>= 1`) и `-f, --follow`;
- слать diagnostics на stderr; failures MUST быть non-zero;
- на Ctrl+C во время follow завершаться кодом 130;
- не спрашивать config/DB/worktree/Python paths заново;
- не вызывать `record_use`, port preflight или `postgres up`;
- не создавать и не менять logfile;
- не добавлять `--grep` / `--errors` / `--since` / JSON snapshot.

Filtering остаётся shell composition: `odcli logs | rg ERROR`.

#### Scenario: Logs inside worktree

- **WHEN** `odcli logs` runs inside a registered worktree with a readable configured logfile
- **THEN** last 100 lines of that file are printed on stdout

#### Scenario: Logs follow

- **WHEN** `odcli logs --follow` runs
- **THEN** CLI streams appended lines until interrupted and exits 130 on Ctrl+C

#### Scenario: Logs missing logfile

- **WHEN** bound `logfile` is absent, empty, missing or unreadable
- **THEN** non-zero error with the resolved path/reason; no file is created

#### Scenario: Invalid tail

- **WHEN** `odcli logs --tail 0` runs
- **THEN** deterministic non-zero error

### Requirement: `odcli shell`

`odcli shell` MUST follow the behavior below.

```bash
odcli shell
odcli --env <environment-id> shell -- --log-level=debug
```

Алгоритм:

1. Выполнить тот же selector/config/Python preflight, что и `run`, без HTTP port check и без `sync_python`.
2. Использовать БД, привязанную к environment: source DB для `shared`, target DB для `copy`.
3. Построить обычный `OdooInstance` через `from_environment()`.
4. Вызвать `OdooInstance.shell()` с `[recorded-python, odoo-bin]`, одним config/DB. Passthrough config/database overrides запрещены.
5. Наследовать stdin/stdout/stderr, signals и exit code штатного `odoo-bin shell`.

#### Scenario: Shell from worktree

- **WHEN** `odcli shell` inside registered worktree
- **THEN** environment + DB inferred, `instance.shell()` executes with bound config/DB

### Requirement: `odcli doctor`

`odcli doctor` MUST follow the behavior below.

```bash
odcli doctor
odcli doctor --json
odcli --project /path/to/repo doctor
```

Read-only checks покрывают manifest, worktrees, `uv`, recorded Python/ownership, dependencies, Odoo/config, catalog, DB/backups, ports и orphaned artifacts.

`doctor` — CLI coordinator над `list`/`get` и filesystem checks, plus internal catalog events. Это не `client.doctor` и не public resource.

Errors дают non-zero; warnings остаются в output. `doctor --fix` не добавляется.

#### Scenario: Doctor detects missing worktree

- **WHEN** `odcli doctor` для environment с missing worktree
- **THEN** warning/error в output, non-zero if error

#### Scenario: Doctor detects missing generated config

- **WHEN** `odcli doctor` для environment с missing generated `odoo.conf`
- **THEN** warning в output

#### Scenario: Doctor detects missing uv

- **WHEN** `odcli doctor` и `uv` not found in PATH
- **THEN** warning/error в output

#### Scenario: Doctor detects recorded Python missing or ownership mismatch

- **WHEN** `odcli doctor` для environment где recorded Python path не существует OR ownership flag mismatched (owned=true но path outside environment root)
- **THEN** warning/error в output

#### Scenario: Doctor detects missing dependency lock

- **WHEN** `odcli doctor` для environment с missing `requirements.lock`
- **THEN** warning в output

#### Scenario: Doctor detects orphaned artifacts

- **WHEN** `odcli doctor` и environment directory существует в `user_data_dir/environments/` но нет matching catalog row
- **THEN** warning об orphaned artifact

#### Scenario: Doctor detects occupied port

- **WHEN** `odcli doctor` для environment с allocated port и `socket.bind` fails
- **THEN** port-occupied в output (diagnostic, не error)

#### Scenario: Doctor detects missing owned backup

- **WHEN** `odcli doctor` для copy environment где owned backup file missing
- **THEN** warning в output

#### Scenario: Doctor shows migrated legacy DB

- **WHEN** `odcli doctor` после cache→data migration
- **THEN** legacy DB shown как migrated legacy artifact

### Requirement: Automation commands

`eval`, `exec`, `test`, `module`, `translations export`, `deps verify` и `vscode generate` MUST входить в CLI. Новых public resources MUST NOT добавляться. RPC fallback MUST NOT использоваться. `eval`/`exec`/`test`/`module`/`translations` MUST использовать `run_shell_script()` или существующий exclusive variant того же Odoo shell primitive.

#### Scenario: Help lists automation commands

- **WHEN** `odcli --help` runs
- **THEN** shows `eval`, `exec`, `test`, `module`, `translations`, `deps`, `vscode`

### Requirement: `eval` and `exec`

```bash
odcli eval "env['res.users'].search_count([])"
odcli exec ./script.py -- arg1 arg2
```

- `eval` MUST вычислять одно Python expression в Odoo shell context (`env`, `odoo`, `self`) и возвращать scalar/collection JSON либо typed recordset summary `{model, ids, count}`; unknown objects MUST получать bounded sanitized `repr`.
- `exec` MUST читать explicit file (`-` означает caller stdin), передавать script через shell stdin и устанавливать predictable `sys.argv` из tokens после `--`.
- default MUST быть best-effort shell rollback. Explicit `--commit` MUST быть виден в plan.
- project config MUST NOT автоматически подставлять eval/exec source.

#### Scenario: Eval expression

- **WHEN** `odcli eval "1+1"` executes in a ready environment
- **THEN** JSON result is returned, no RPC used

### Requirement: `module` commands

```bash
odcli module list --state installed
odcli module update comerta_base --dry-run
odcli module update comerta_base --yes
odcli module test comerta_base --test-tags /comerta_base --reload-tests
```

- `list [MODULE...]` MUST читать `ir.module.module`.
- `update` MUST требовать `--yes`; внутри shell `button_immediate_upgrade()`.
- `test` MUST remain a backward-compatible alias to the top-level local Odoo test operation, call Odoo 19 `odoo.tests.shell.run_tests(...)` through the same single runner path, enforce workers=0 and the free bound HTTP port, and exit non-zero for failed/error tests and zero tests unless `--allow-empty`.
- install/uninstall MUST NOT добавляться. Public `ModuleResource` or `TestResource` MUST NOT существовать.

#### Scenario: Module list

- **WHEN** `odcli module list --state installed` executes
- **THEN** installed modules are listed via local Odoo shell

#### Scenario: Module test compatibility

- **WHEN** an existing caller invokes `odcli module test comerta_base --test-tags /comerta_base`
- **THEN** the request reaches the same preflight, `OdooTestSpec`, runner, and result path as the top-level command without a duplicate implementation

### Requirement: `translations export`

```bash
odcli translations export --module comerta_base --language ru_RU
```

Команда MUST подавать exporter через non-TTY stdin в `run_shell_script()`. MUST NOT использовать `--shell-file`. PO имя MUST браться из wizard `name`/`tools.get_iso_codes()` (`ru_RU` → `ru.po`). Public `TranslationResource` MUST NOT существовать.

#### Scenario: ru_RU writes ru.po

- **WHEN** `odcli translations export --module comerta_base --language ru_RU` succeeds
- **THEN** file `ru.po` is written, not `ru_RU.po`

### Requirement: `deps verify`

```bash
odcli deps verify
odcli deps verify --json
```

Команда MUST запускать `uv pip check` плюс imports из addon `external_dependencies['python']` в recorded interpreter.

#### Scenario: Missing import reported

- **WHEN** an addon declares `external_dependencies['python']` import that is missing
- **THEN** `deps verify` reports module/import name and exits non-zero

### Requirement: `vscode generate`

```bash
odcli vscode generate
odcli vscode generate --write
```

Команда MUST выполнять обратное преобразование current project/environment в debugpy launch profile. Default MUST печатать; `--write` MUST создавать отсутствующий `.vscode/launch.json` и MUST NOT rewrite существующего JSONC. MUST требовать ready environment.

#### Scenario: Print profile

- **WHEN** `odcli vscode generate` runs for a ready environment
- **THEN** one debugpy profile is printed and no file is written

### Requirement: Instance commands share one ready path

`run`, `logs`, `shell`, `eval`, `exec`, `module`, `translations`, `deps verify` и `vscode generate` MUST получать `OdooClient`, ready environment и `OdooInstance` через один internal `ready_instance` path. Command bodies MUST NOT копировать resolve/verify/`from_environment()` и MUST NOT конструировать `OdooClient` сами.

`ready_instance` MUST использовать already-specified two-rule context. Port preflight остаётся только у `run`.

#### Scenario: Eval and run share resolve

- **WHEN** `odcli eval 1` and `odcli run` execute inside a registered worktree
- **THEN** both resolve the same environment through `ready_instance`, not through per-command helpers

### Requirement: CLI does not open the catalog

CLI command bodies, printers и env-list rendering MUST NOT вызывать `get_catalog()` и MUST NOT писать `last_used_at` или environment events напрямую.

`odcli run` MUST вызвать `EnvironmentResource.record_use()` после free-port preflight и MUST NOT вызывать его при `port-conflict`. Other instance commands MUST NOT record `use`.

JSON envelope v1 MUST остаться: `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, `warnings`; success — одинаковые `result` и `data`; error — `error.code` + sanitized `error.message`. Один shared emit path.

Entry point MUST остаться `odoo_instance_sdk.cli:cli`. Имена команд и `from odoo_instance_sdk.cli import cli` MUST сохраниться.

#### Scenario: List JSON does not open catalog

- **WHEN** `odcli env list --json` prints the envelope
- **THEN** the command does not call `get_catalog()` and does not write environment events

#### Scenario: Port conflict skips use

- **WHEN** `odcli run` hits an occupied port
- **THEN** output is `port-conflict` / ownership-unknown and `record_use` is not called

#### Scenario: Successful run records use on the environment resource

- **WHEN** `odcli run` finds a free port
- **THEN** `EnvironmentResource.record_use()` writes `last_used_at` and `use/succeeded` before `run_foreground()`

#### Scenario: Help still lists full command surface

- **WHEN** `odcli --help` runs
- **THEN** shows init, env, run, logs, shell, doctor, monitor, eval, exec, module, translations, deps, vscode

### Requirement: `odcli env list`

```bash
odcli env list [--format rich|json|toon]
odcli env list --all [--format rich|json|toon]
odcli env list --all-projects [--format rich|json|toon]
odcli env list --watch [--interval SECONDS]
```

The command SHALL invoke `EnvironmentMonitor.snapshot()` exactly once per one-shot rendering and once per live refresh, receiving one complete typed inventory. It SHALL NOT instantiate `OdooClient` to read backups/environments, query `BackupCatalog`, run Git/Docker/filesystem reconciliation, probe ports, regroup catalog rows into an alternative model, or perform any other collection after the snapshot returns. The renderer MAY group and sort the returned typed objects for presentation.

Rich output SHALL group by project: one project header and cluster summary followed by a Rich `Table` containing the environment rows for that project. It SHALL preserve the information represented by these columns: `NAME`, `BRANCH`, `STATE`, `RUNTIME`, `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, `GIT_AHEAD`, `GIT_DIFF`, `SIZE`, `DB_MODE`, `DATABASE`, `PORT`, and `ARTIFACTS`; responsive Rich layout MAY combine labels visually but SHALL NOT omit values. Rich color/ANSI SHALL be enabled only when supported by the output terminal.

`--all-projects` and project-context behavior SHALL remain unchanged. `--all` SHALL request `include_removed=True` only for Rich output so the existing observable contract remains: Rich includes removed rows, while JSON/TOON wrap the default non-removed `Snapshot`. JSON and TOON SHALL use `command="env.list"` and the same monitor snapshot contract as `GET /api/v1/snapshot`; TOON differs only in serialization.

Cluster, runtime/PID/resources, Git activity, storage, port observation, and artifact/backup availability SHALL all come from the returned snapshot. A project containing only removed rows MAY appear only when `include_removed=True`; its `environment_count` SHALL count the rows included in that result.

#### Scenario: Grouped by project with cluster header

- **WHEN** `env list` runs with two projects
- **THEN** output has two `Project <name>` headers each followed by a `PostgreSQL ...` cluster summary line, then that project's environment rows

#### Scenario: JSON parity with monitor snapshot

- **WHEN** `odcli env list --json --all-projects` runs
- **THEN** `result`/`data` payload uses the same `projects[].cluster` and `environments[].runtime` contract as `EnvironmentMonitor.snapshot()` and `GET /api/v1/snapshot`

#### Scenario: --all human includes removed, JSON does not

- **WHEN** `odcli env list --all` prints human table and `odcli env list --json --all` emits JSON
- **THEN** human table includes `STATE=removed` rows; JSON `result.environments` contains only non-removed snapshot rows

#### Scenario: Grouped Rich table uses one result

- **WHEN** Rich `env list` runs with two projects
- **THEN** output has two project sections and all displayed metrics/reconciliation fields originate from one `EnvironmentMonitor.snapshot()` result

#### Scenario: JSON and TOON parity with monitor snapshot

- **WHEN** `odcli env list --format json --all-projects` and `--format toon --all-projects` render the same sample
- **THEN** decoded `result`/`data` equal the JSON-safe `EnvironmentMonitor.snapshot()` object including cluster, runtime, observation, and artifacts

#### Scenario: --all compatibility

- **WHEN** `odcli env list --all` renders Rich and `odcli env list --all --format json` or `--format toon` renders a machine document
- **THEN** Rich includes `lifecycle_state="removed"` rows while both machine documents contain only non-removed rows

#### Scenario: CLI does not recollect inventory

- **WHEN** the `env list` command and renderers are exercised with a supplied typed snapshot
- **THEN** no CLI code opens the catalog, lists backups/environments, calls Git or Docker, probes a port, or performs filesystem reconciliation

### Requirement: `odcli postgres status`

```bash
odcli postgres status [--json]
```

`status` MUST быть read-only (не меняет cluster state). `status` MUST NOT вызывать Docker в external mode (только TCP probe).

Human и `--json` output дополнительно возвращают read-only cluster container fields (parity с monitor cluster snapshot): container ID/name/image, Docker-reported init PID + PID scope, CPU/memory/volume metrics, `sampled_at`, `unavailability_reason`.

`postgres status` MUST call both `cluster.status()` and `cluster.resource_snapshot()`, then emit a `ClusterSnapshot`-shaped object. External → `unavailability_reason="external_not_owned"`. Stopped/missing/docker-unavailable — diagnostic exit 0.

#### Scenario: Status JSON envelope with container fields

- **WHEN** `odcli postgres status --json` runs on a healthy compose cluster
- **THEN** JSON envelope v1 `result` contains `state`, `mode`, `owned`, `endpoint`, `container`, `metrics`, `sampled_at`

#### Scenario: Status external does not invoke Docker

- **WHEN** `odcli postgres status` on external mode
- **THEN** only TCP probe is performed, container/resource fields `null` with `unavailability_reason="external_not_owned"`

#### Scenario: Parity with monitor cluster snapshot

- **WHEN** `odcli postgres status --json` and `odcli monitor --headless` `GET /api/v1/snapshot` run in the same instant for the same project
- **THEN** container PID/resource values match between the two outputs

### Requirement: `odcli postgres` command group

`odcli` MUST предоставлять command group `postgres` с подкомандами:

```text
odcli postgres status [--json]
odcli postgres up [--wait-timeout SECONDS]
odcli postgres stop [--timeout SECONDS]
odcli postgres approve-image --image-digest REPOSITORY@sha256:DIGEST [--timeout SECONDS] [--json]
```

Все три MUST использовать existing project resolution rules (`resolve_project_path`) — без project argument внутри initialized project или registered worktree.

`status` MUST быть read-only (не меняет cluster state). `status` MUST NOT вызывать Docker в external mode (только TCP probe). Human и `--json` output дополнительно возвращают read-only cluster container fields (parity с monitor cluster snapshot): container ID/name/image, Docker-reported init PID + PID scope, CPU/memory/volume metrics, `sampled_at`, `unavailability_reason`.

`postgres status` MUST call both `cluster.status()` and `cluster.resource_snapshot()`, then emit a `ClusterSnapshot`-shaped object. External mode — container/resource fields `null` with `unavailability_reason="external_not_owned"`. Stopped/missing/docker-unavailable — diagnostic exit 0.

`up` MUST быть idempotent. Для managed (compose) cluster — вызывает `PostgresCluster.ensure_running(timeout)` (Compose `up --detach --wait`). Для external cluster — только reachability check (вызывает `status()`), не вызывает Docker. `--wait-timeout SECONDS` переходит в `ensure_running(timeout=...)`.

`stop` MUST быть allowed только для SDK-owned (compose) cluster. Для external — typed error, exit 1. `--timeout SECONDS` переходит в `stop(timeout=...)`. `stop` MUST preserves container data/volume (никогда `down -v`).

JSON envelope v1 MUST остаться (`emit_json_envelope`/`fail`). Entry point `odoo_instance_sdk.cli:cli` MUST сохраниться.

`postgres` group MUST NOT дублировать preflight, который уже делает `OdooInstance` перед spawn Odoo. Команды `run`/`shell`/`eval`/`exec`/`module`/`translations` не вызывают `postgres up` явно — preflight в `OdooInstance` обрабатывает readiness.

`approve-image` MUST resolve the manifest reference through Docker within its bounded `--timeout`, require `--image-digest` to exactly equal the OCI RepoDigest, and persist the approval outside the repository. Human and JSON responses MUST show the exact reference and digest. `up` and Odoo preflight MUST fail closed until approval exists and MUST re-resolve the image at every start.

#### Scenario: Status inside initialized project

- **WHEN** `odcli postgres status` runs inside a project with `[postgres] mode="compose"`
- **THEN** output reports `state`, `mode`, `owned`, `endpoint`, container ID/name/image/PID+scope, CPU, memory, optional volume without starting/stopping cluster

#### Scenario: Status JSON envelope with container fields

- **WHEN** `odcli postgres status --json` runs on a healthy compose cluster
- **THEN** JSON envelope v1 `result` contains `state`, `mode`, `owned`, `endpoint`, `container`, `metrics`, `sampled_at`

#### Scenario: Status external does not invoke Docker

- **WHEN** `odcli postgres status` on external mode
- **THEN** only TCP probe is performed, container/resource fields `null` with `unavailability_reason="external_not_owned"`, no `docker compose`/`docker inspect` invocation

#### Scenario: Status stopped compose

- **WHEN** `odcli postgres status` on a stopped compose cluster
- **THEN** `state=stopped`, container/resource fields `null`, `unavailability_reason="stopped"`, exit 0

#### Scenario: Docker unavailable is diagnostic not error

- **WHEN** `odcli postgres status` on compose mode and `docker` not in PATH
- **THEN** `unavailability_reason="docker_unavailable"`, exit 0 (not 1)

#### Scenario: Parity with monitor cluster snapshot

- **WHEN** `odcli postgres status --json` and `odcli monitor --headless` `GET /api/v1/snapshot` run in the same instant for the same project
- **THEN** container PID/resource values match between the two outputs

#### Scenario: Up compose starts cluster

- **WHEN** `odcli postgres up --wait-timeout 60` on compose mode with `STOPPED` cluster
- **THEN** runs `docker compose up --detach --wait`, polls until healthy, exits 0

#### Scenario: Up external checks reachability only

- **WHEN** `odcli postgres up` on external mode with reachable endpoint
- **THEN** no Docker invocation, exits 0

#### Scenario: Up external unreachable fails

- **WHEN** `odcli postgres up` on external mode with unreachable endpoint
- **THEN** exits 1 with typed `PostgresClusterUnreachableError` message

#### Scenario: Stop compose preserves volume

- **WHEN** `odcli postgres stop --timeout 30` on a running compose cluster
- **THEN** runs `docker compose stop`, named volume persists, exits 0

#### Scenario: Stop external fails

- **WHEN** `odcli postgres stop` on external mode
- **THEN** exits 1 with `PostgresClusterNotOwnedError` message

#### Scenario: Commands resolve project without --project

- **WHEN** `odcli postgres status` runs inside an initialized project
- **THEN** project is resolved via existing two-rule context, no `--project` required

### Requirement: `odcli monitor` command

```bash
odcli monitor [--headless] [--host HOST] [--port PORT] [--no-open]
```

`odcli monitor` MUST запускать FastAPI server с `GET /api/v1/snapshot` (typed `Snapshot` JSON, optional `?project_id=`) и `GET /healthz` (`{"status":"ok"}`).

Default UI mode: serves API + React SPA, bind `127.0.0.1`, auto port `8069` then `8100`–`8120` (never `8070`–`8099`), opens browser unless `--no-open`. `--headless`: API only, no static mount, no browser. Built-in server accepts only loopback bind addresses (`127.0.0.1`, `localhost`, `::1`) and loopback HTTP Host headers because it has no authentication. Requires `dashboard` extra (`pip install odoo-instance-sdk[dashboard]`); missing extra → exit 1 with actionable hint.

#### Scenario: Default UI mode serves SPA and API

- **WHEN** `odcli monitor` runs without `--headless`
- **THEN** FastAPI serves `/api/v1/snapshot`, `/healthz` and the React SPA; browser opens on `http://127.0.0.1:<port>/`

#### Scenario: Headless serves API only

- **WHEN** `odcli monitor --headless --no-open` runs
- **THEN** `/api/v1/snapshot` and `/healthz` respond; static assets not mounted; browser not opened

#### Scenario: Missing dashboard extra actionable hint

- **WHEN** `odcli monitor` runs and `fastapi`/`uvicorn` not installed
- **THEN** exits 1 with message containing `pip install odoo-instance-sdk[dashboard]`

### Requirement: `init` wires `--postgres*` options

`odcli init` MUST принимать `--postgres`, `--postgres-image`, `--postgres-port`, `--postgres-user` (см. `project-init` spec). `init` MUST NOT создавать compose artifacts directory. `init` MUST NOT запускать Docker. Existing init flow (interactive prompts, `--no-input`, `--dry-run --json`, idempotency, VS Code import) MUST оставаться без breaking changes — новые опции интегрируются в existing provenance tracking и `ProjectConfig` construction.

#### Scenario: Init with postgres and vscode import

- **WHEN** `odcli init --from-vscode launch.json --postgres compose --postgres-image ...` runs
- **THEN** both VS Code import and postgres section are persisted; provenance records both sources

#### Scenario: Init provenance records postgres option

- **WHEN** `odcli init --postgres compose --postgres-image ... --dry-run --json` runs
- **THEN** provenance includes `option` entry for `postgres`

### Requirement: Lightweight CLI transport boundary

The CLI SHALL remain a Click inbound adapter with `odoo_instance_sdk.cli:cli` as the stable import and entry point. `cli.py` SHALL own registration and composition, while `commands/context.py`, `commands/output.py`, and `commands/env.py` SHALL own only the affected CLI context, output policy, and environment command adapter responsibilities.

On the affected canonical environment inventory path, `EnvironmentMonitor` and its reusable private collectors SHALL return typed operation results and SHALL NOT import Click or FastAPI, print output, inspect transport flags, or return CLI-envelope or React-shaped dictionaries. Existing result-building inside unrelated legacy `--json` callbacks MAY remain while those callbacks adopt the shared CLI envelope/emitter. CLI callbacks MAY parse syntax, resolve CLI context, select output mode, invoke public resources, render the result, and map SDK exceptions to exit codes. The change SHALL NOT add a DI container, command bus, handler registry, renderer interface/registry/DSL, generic application/service/provider layer, or a second implementation interface.

Unrelated command groups SHALL remain in their current modules unless the output helper must be reused; empty or symmetry-only modules SHALL NOT be created.

#### Scenario: Resource operation is transport independent

- **WHEN** `EnvironmentMonitor.snapshot()` is invoked from Python without the CLI
- **THEN** it returns the same typed inventory consumed by `env list` and imports neither Click nor FastAPI

#### Scenario: Stable Click entry point

- **WHEN** callers import `cli` from `odoo_instance_sdk.cli` or execute the installed `odcli` script
- **THEN** the same Click command tree is available through `odoo_instance_sdk.cli:cli`

#### Scenario: No speculative framework

- **WHEN** the CLI boundary implementation is inspected
- **THEN** it contains direct Click-to-resource composition and no generic registry, command bus, DI container, or single-implementation provider hierarchy

### Requirement: Typed CLI context on affected paths

The root Click callback SHALL create one small typed CLI context carrying the explicit project selector, explicit environment selector, resolved project/environment values when available, and their provenance. A native Click typed passing mechanism such as `make_pass_decorator` SHALL replace direct untyped `ctx.obj` dictionary access on every path touched by this change.

Reusable resolvers and workflows SHALL accept Python values and SHALL NOT accept `click.Context` or read transport flags. Existing project and environment resolution order and provenance values SHALL remain unchanged. Migrating an unrelated callback from `ctx.obj` is out of scope unless required to keep a shared resolver correct.

#### Scenario: Explicit project provenance survives typed context

- **WHEN** `odcli --project /path/to/repo env list --format json` resolves the project
- **THEN** the envelope reports `project_source="explicit"` without a callback reading a dictionary key from `ctx.obj`

#### Scenario: Resolver is Click-free

- **WHEN** a project or environment resolver is imported and called in a Python test
- **THEN** its signature contains no `click.Context` and it resolves from typed Python inputs

### Requirement: Live Rich environment inventory

`odcli env list` SHALL accept `--watch` and `--interval SECONDS`. `--watch` SHALL be valid only for `rich` mode when stdout is an interactive TTY. `--interval` SHALL default to `2.0` and SHALL reject values below `0.1` as a Click usage error with exit code `2`.

The live loop SHALL use `rich.live.Live` and repeatedly invoke the same `EnvironmentMonitor.snapshot(project_id=..., include_removed=...)` query used by the one-shot command. Every refresh SHALL retain the original project selection, `--all`, `--all-projects`, and deterministic project/environment ordering; this change SHALL NOT add a separate live query or a new sort option.

After at least one successful sample, a collection failure SHALL keep the last successful table visible, display a sanitized diagnostic in the live region or stderr, and retry at the selected interval. Failure of the initial sample SHALL exit `1`. The loop SHALL use `Live(..., transient=True)` or explicit equivalent cleanup. `Ctrl-C` SHALL stop polling, close the Live context, restore the terminal, remove the live region and last table, leave no task/thread/process behind, and exit `130`.

The live renderer SHALL use `Table` and `Live`; it SHALL use `Status` or `Progress` only for an operation with real measurable progress and SHALL NOT show a fabricated progress bar during snapshot polling.

#### Scenario: Watch refreshes the canonical query

- **WHEN** `odcli env list --watch --interval 2` runs in an interactive terminal
- **THEN** one Rich live table is refreshed from successive canonical inventory snapshots with the original filters and deterministic ordering

#### Scenario: Watch rejects machine output

- **WHEN** `odcli env list --watch --format json` or `--format toon` is invoked
- **THEN** Click exits `2` without starting a live loop or emitting a partial machine document

#### Scenario: Watch rejects non-interactive output

- **WHEN** `odcli env list --watch` is invoked with stdout redirected or captured
- **THEN** the command exits `1` with a sanitized diagnostic and leaves stdout free of a partial live display

#### Scenario: Later sample failure retains data

- **WHEN** a successful live sample is followed by a monitor failure
- **THEN** the last successful inventory remains displayed and the loop retries without replacing it with an empty graph

#### Scenario: Watch interrupt cleans up

- **WHEN** the user presses Ctrl-C during live refresh or interval waiting
- **THEN** Rich restores the terminal, removes the live region so the last table is not left in scrollback, the polling loop exits, no background work remains, and the command exits `130`

### Requirement: CLI compatibility characterization

Before move-only changes, automated characterization tests SHALL pin the root/subcommand help and command tree, exit codes `0`, `1`, `2`, and `130`, stdout/stderr routing, JSON success/error envelope v1, `--json` behavior, cwd/project/environment resolution, `env list --all`/`--all-projects`, redaction, public imports, and native streams for `run`, `shell`, and `logs --follow`.

Move-only and semantic output/watch changes SHALL be kept in separate commits on `feat/MYL-55-cli-output-boundary` so regressions can be attributed independently.

#### Scenario: Passthrough streams remain native

- **WHEN** characterization tests execute `run`, interactive `shell`, or `logs --follow`
- **THEN** their stdin/stdout/stderr and exit/interrupt behavior match the pre-change contract and no envelope or Rich live wrapper is introduced

#### Scenario: Help tree remains stable

- **WHEN** characterization tests compare root and subcommand help after the refactor
- **THEN** all existing command names and required options remain present, with only the specified additive format/watch options

### Requirement: Top-level Odoo test command and shared adapter

The Click command tree SHALL add this bounded structured command:

```text
odcli test [TARGET] [--tags TAGS] [--reload-tests] [--allow-empty]
           [--changed [--base REF] [--dry-run]] [--format rich|json|toon] [--json]
```

`odcli test` SHALL resolve the typed MYL-55 CLI project/environment context, delegate selection and execution to the `local-odoo-testing` capability, and render through the shared MYL-55 output adapter. `TARGET` SHALL be optional and singular. `--changed` with `TARGET`, `--base` without `--changed`, `--dry-run` without `--changed`, and a test-file target with `--tags` SHALL be Click usage errors with exit code `2` before selection or execution.

The command SHALL be added beside the existing command groups through the stable `odoo_instance_sdk.cli:cli` registration/composition entry point. The test adapter SHALL live in focused `commands/test.py`; reusable selection/preflight/execution helpers SHALL not import Click, Rich, or the CLI envelope.

#### Scenario: Root help exposes test command

- **WHEN** `odcli --help` and `odcli test --help` run
- **THEN** the root lists `test` and its help exposes target, native tags, changed/base/dry-run, reload, empty-result, and shared format options

#### Scenario: Invalid changed combination is a usage error

- **WHEN** `odcli test sale --changed` is invoked
- **THEN** Click exits `2` before environment selection, Git collection, preflight, or Odoo execution

### Requirement: Test output uses the shared CLI contract

Both `odcli test` and `odcli module test` SHALL be bounded structured leaves under the MYL-55 `OutputMode` and CLI envelope v1 contract. They SHALL accept command-local `--format rich|json|toon`, keep `--json` as the alias for `--format json`, reject conflicting format flags through the shared option resolver, and use the shared sanitized error/exit mapping. The command name in new-path envelopes SHALL be `test`; the compatibility path SHALL retain `module.test` while `result` and execution semantics remain equal for equivalent inputs.

Every success machine result SHALL contain resolved environment identity, selector kind/value and provenance, modules, and exit code. An executed result SHALL additionally contain effective native test tags, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags from `OdooTestResult`. A successful `--changed --dry-run` result SHALL instead contain `dry_run=true` plus complete base/Git provenance and SHALL omit `test_tags`, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags. A successful changed selection with no addons SHALL contain `reason="no_addon_changes"`, complete base/Git provenance, empty modules, and `exit_code=0`, and SHALL omit those same execution-only fields; if it is also a dry-run it MAY additionally contain `dry_run=true`. Neither non-executed state SHALL construct or imply an `OdooTestResult`, fabricate zero counts/false flags, or emit execution progress. JSON and strict-decoded TOON SHALL be semantically equal in all three states. Raw sanitized Odoo diagnostics SHALL be written only to stderr; machine stdout SHALL contain exactly one document without ANSI, prompts, progress, or embedded raw logs.

Rich output SHALL show the same resolved environment, selection/modules, and exit status. It SHALL show final counts only for executed results; `--changed --dry-run` and `no_addon_changes` MAY use a Rich table but SHALL not display fabricated counts or execution progress.

#### Scenario: JSON and TOON test parity

- **WHEN** equivalent frozen test results are emitted with `--format json` and `--format toon`
- **THEN** decoded envelope-v1 values are equal, stdout contains one document, diagnostics are only on stderr, and both commands return the typed result's exit code

#### Scenario: Changed no-op is a successful document

- **WHEN** `odcli test --changed --format json` finds only docs/non-addon paths
- **THEN** stdout contains one success envelope with `reason="no_addon_changes"`, selected modules is empty, and no Odoo diagnostics/process are produced

#### Scenario: Dry-run machine shape has no execution fields

- **WHEN** `odcli test --changed --dry-run --format json` safely selects one or more addons
- **THEN** stdout contains selection/base provenance, modules, `dry_run=true`, and `exit_code=0`, omits tags/options/counts/failure/zero flags, and produces no execution progress or Odoo diagnostics

### Requirement: `module test` is a compatibility alias

`odcli module test MODULE...` SHALL remain available with its existing plural positional module form and existing `--test-tags`, `--reload-tests`, `--allow-empty`, `--json`, and MYL-55 `--format` options. It SHALL validate each module through the same eligible-addon boundary, build the same `OdooTestSpec`, use the same installed-state preflight and single runner, and return the same `OdooTestResult` as `odcli test MODULE --tags ...` for an equivalent one-module request.

The alias SHALL continue to require at least one module and `--test-tags`. It SHALL not accept cwd/file inference, `--changed`, `--base`, or `--dry-run`, and SHALL not retain a second `run_module_tests` behavior branch after migration.

#### Scenario: Legacy command delegates to the same path

- **WHEN** `odcli module test sale --test-tags /sale --reload-tests` and `odcli test sale --tags /sale --reload-tests` run against the same frozen environment/runner
- **THEN** they perform the same selection validation, preflight, and one runner call and produce equivalent typed results and exit codes

#### Scenario: Legacy plural modules remain supported

- **WHEN** `odcli module test sale stock --test-tags standard` is invoked
- **THEN** both exact eligible addons are sorted/deduplicated into one `OdooTestSpec` and one runner call

### Requirement: `odcli db` command group

The Click adapter SHALL add:

```text
odcli db refresh [--restore] [--reset-admin-password] [--source-branch BRANCH]
odcli db reset-admin-password
```

`commands/db.py` SHALL parse options, resolve project/environment context, call existing public resources, render typed results, and map typed exceptions. It SHALL not download, restore, acquire locks, run ORM scripts, edit manifests, or construct alternate result dictionaries itself. The group SHALL be registered through the stable `odoo_instance_sdk.cli:cli` entry point after rebasing the MYL-55 CLI foundation.

#### Scenario: Help exposes database commands

- **WHEN** `odcli db --help` runs
- **THEN** it lists `refresh` and `reset-admin-password` with the documented options

### Requirement: `odcli db refresh` option and context rules

`db refresh` SHALL require project context through explicit `--project`, nearest manifest, or exact registered worktree. It SHALL source the remote instance only from project `[test_instance]`. `--source-branch` SHALL override its configured branch. `--reset-admin-password` without `--restore` SHALL be a Click usage error with exit code 2 before SDK/network/catalog mutation.

Without `--restore`, the command SHALL download only. With `--restore`, it SHALL request the complete preparation flow. It SHALL not prompt for either master password and SHALL never accept a password option.

#### Scenario: Download-only refresh

- **WHEN** `odcli db refresh` runs in a configured project
- **THEN** it downloads/catalogs a backup and does not touch local databases or the project default

#### Scenario: Reset flag requires restore

- **WHEN** `odcli db refresh --reset-admin-password` runs without `--restore`
- **THEN** Click exits 2 with a usage error before any operation begins

### Requirement: Context-aware `odcli db reset-admin-password`

`db reset-admin-password` SHALL resolve an exact ready environment from `--env` or the current registered worktree using the shared instance-command resolver. It SHALL require the environment's generated config and recorded source/target ownership to identify exactly one database, verify the selected Odoo endpoint is local, and delegate to the existing database resource. It SHALL not choose the latest/only environment by recency or project membership.

#### Scenario: Reset from registered worktree

- **WHEN** the command runs inside one ready registered worktree
- **THEN** it resets that environment's single bound database through the resource and ORM

#### Scenario: Project root is not enough

- **WHEN** the command runs outside a registered worktree without `--env`
- **THEN** it fails with candidate guidance and modifies no database

### Requirement: Database command output and redaction

Database commands SHALL use the accepted MYL-55 output contract: Rich for human structured output and the same CLI envelope for JSON/TOON. Successful refresh output SHALL contain backup ID/path/size/checksum/downloaded timestamp, nullable source branch and branch origin, plus optional restored database, reset/default-switch state, provenance status, warnings, and retained-artifact state. Machine formats SHALL be semantically equal and contain no ANSI or prompt.

Passwords, secret environment values, multipart bodies, complete Odoo config content, and ORM script source SHALL never appear in output, errors, traceback summaries, or Rich renderables. Exit status SHALL follow the foundation's renderer-independent policy.

#### Scenario: Machine refresh output is complete and secret-free

- **WHEN** download-only refresh succeeds in JSON and TOON modes
- **THEN** decoded envelopes contain equal backup/provenance data and neither contains the remote password

#### Scenario: Retained artifact failure output

- **WHEN** reset fails after restore
- **THEN** the failure identifies retained backup/database and unchanged default without including `admin` as a password field/value

### Requirement: Shared confirmation ordering

For commands requiring confirmation, plan construction SHALL occur before confirmation. Machine modes and dry-run SHALL never prompt; dry-run SHALL stop after valid plan emission, while normal machine mode SHALL retain each command's existing explicit-confirmation requirement.

#### Scenario: Dry-run of destructive command

- **WHEN** a destructive command is invoked with `--dry-run` and without its apply/yes flag
- **THEN** the valid plan is emitted without prompting or mutating

#### Scenario: Normal machine removal lacks confirmation

- **WHEN** `env remove` runs in JSON or TOON mode without `--yes` and without `--dry-run`
- **THEN** it retains the existing `confirmation_required` failure and performs no removal

