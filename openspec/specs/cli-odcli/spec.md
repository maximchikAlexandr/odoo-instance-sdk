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

Instance commands MUST resolve one runtime context in this order:

1. An explicit `--env SELECTOR`; failure to resolve it MUST be terminal and MUST NOT fall back.
2. The exact registered worktree containing the current directory.
3. An explicit `--project PATH`, or otherwise the nearest initialized project manifest found upward from the current directory to the Git/filesystem boundary.
4. Otherwise an actionable context-resolution error.

The first two cases produce an environment context; the third produces a project context. Resolution MUST NOT select an environment by recency or because it is the only ready environment. Project fallback MUST NOT create or catalogue a synthetic environment.

#### Scenario: Explicit environment wins

- **WHEN** an instance command receives a valid explicit `--env` while current directory is inside an initialized project
- **THEN** it uses the selected environment and does not fall back to project context

#### Scenario: Invalid explicit environment does not fall back

- **WHEN** an instance command receives an unknown or ambiguous explicit `--env`
- **THEN** it fails with the environment resolution error before project resolution or runtime work

#### Scenario: Exact worktree wins over project

- **WHEN** an instance command runs inside an exact registered worktree with no explicit environment
- **THEN** project and environment are inferred from that worktree record

#### Scenario: Main checkout uses project context

- **WHEN** `odcli run` executes in an initialized main checkout with no explicit environment and no exact worktree match
- **THEN** it resolves the nearest project manifest and uses project context

#### Scenario: Project is not an environment

- **WHEN** an instance command resolves project context
- **THEN** no environment record is created, selected, or added to `odcli env list`

#### Scenario: Inside registered worktree

- **WHEN** `odcli run` executes inside an exact registered worktree
- **THEN** project and environment are inferred from the worktree record

#### Scenario: Outside worktree without flags

- **WHEN** `odcli run` executes outside an initialized project and registered worktree without `--env` or `--project`
- **THEN** it fails with guidance to initialize/select a project or select/cd into an environment

#### Scenario: Single ready not silently selected

- **WHEN** a project has exactly one ready environment, current directory is not in its worktree, and no `--env` is supplied
- **THEN** that environment is never selected implicitly and project fallback is used only when the project itself is initialized

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

Instance commands (`run`, `logs`, `shell`, `eval`, `exec`, `test`, `module`, `translations`, `deps verify`, and `vscode generate`) MUST consume the shared `environment | project` resolver. Commands whose required state is available from either context MUST operate on both. A command that requires environment-owned state or lifecycle metadata MUST reject project context with an actionable error and MUST NOT fabricate an environment.

Test target, working-directory, and addon resolution MUST begin only after runtime context is resolved and MUST NOT select a different environment or project.

#### Scenario: Explicit environment precedes addon selection

- **WHEN** `odcli --env <uuid> test sale` runs
- **THEN** environment resolution completes before addon selection

#### Scenario: Project-capable command accepts main checkout

- **WHEN** a project-capable instance command runs under an initialized main checkout without `--env`
- **THEN** it uses the project runtime configuration

#### Scenario: Environment-only command rejects project context

- **WHEN** a command requiring environment-owned artifacts resolves only a project context
- **THEN** it returns an actionable error without catalog mutation or subprocess launch

#### Scenario: Explicit --env

- **WHEN** `odcli --env <uuid> test sale` runs
- **THEN** the environment is resolved from the explicit selector before addon selection

#### Scenario: Ambiguous name

- **WHEN** `odcli --env "feat" test sale` matches two environments
- **THEN** it fails with the candidate list and performs no addon, Git, preflight, project fallback, or Odoo work

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

The exact bounded structured leaf inventory is: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `backup list`, `backup show`, `backup validate`, `backup delete`, `db refresh`, `db reset-admin-password`, `db list`, `db restore`, `db drop`, `resource list`, `resource doctor`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `db locks`, `db stats`, `db bloat`, `db init-monitoring`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Each SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Existing `--json` SHALL remain a backward-compatible alias for `--format json`. Supplying `--json` with `--format toon` or `--format rich` SHALL be a Click usage error with exit code `2`; supplying `--json --format json` SHALL be accepted. During normal execution, `run`, interactive `shell`, `psql`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit document output or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; those dry-run paths SHALL suppress native execution and emit one bounded plan document in Rich, JSON, or TOON, with `--json` equivalent to `--format json`. `psql --dry-run` SHALL remain an explicit plan-only exception that emits the shared sanitized native command plan without spawning; normal `psql` remains raw passthrough and SHALL continue to reject `--format` and `--json`.

The CLI SHALL define one CLI-only `OutputMode` with values `rich`, `json`, and `toon`. The mode and envelope types SHALL NOT become public SDK models or FastAPI response models. Each successful or failed bounded operation SHALL first build one JSON-safe CLI envelope v1 containing `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, and `warnings`; success SHALL contain equal `result` and `data`, while failure SHALL omit top-level `result` and `data` and SHALL contain stable `error.code` and sanitized `error.message`. `error` MAY additionally contain an operation-specific JSON-safe `details` field; failures without structured details SHALL omit it and retain their existing v1 shape.

JSON and TOON SHALL serialize that exact envelope without building format-specific result graphs. Decoding a TOON document with the selected strict decoder SHALL yield the same JSON value as decoding JSON output for the same operation. Machine modes SHALL emit exactly one UTF-8 document to stdout with no ANSI, prompt, status, progress, or external log text; diagnostics SHALL go to stderr. Renderer selection SHALL NOT change operation execution, exception mapping, or exit code. Native Click parse failures that occur before output-mode resolution SHALL retain Click's stderr usage output and exit code `2`.

For `env remove`, `backup delete`, `db restore`, and `db drop`, JSON and TOON document modes (including the `--json` alias) SHALL never call `click.confirm`. Without `--yes`, they SHALL NOT execute mutation and SHALL emit exactly one sanitized failure envelope with `error.code="confirmation_required"` and exit code `1`. With `--yes`, JSON and TOON SHALL execute the same operation and normal success/failure mapping. Interactive Rich mode SHALL retain command-specific confirmation behavior. Dry-run SHALL never prompt and SHALL remain non-mutating.

Rich renderers SHALL remain adjacent to the concrete commands whose typed results they render. They MAY use `Table`, `Status`, `Progress`, and `Live` only when appropriate to the operation; they SHALL NOT introduce a generic renderer interface, registry, or DSL. `db stats` and `db bloat` SHALL render separate tables and indexes tables rather than one sparse combined table.

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
- **THEN** stdout contains one JSON or TOON envelope and the diagnostic is written only to stderr

#### Scenario: Structured failure details preserve the failure variant

- **WHEN** a bounded operation has structured diagnostics required by its capability contract
- **THEN** the machine document has `ok=false`, omits top-level `result` and `data`, retains `error.code` and sanitized `error.message`, and stores those diagnostics only in JSON-safe `error.details`
- **AND** a failure without such diagnostics omits `error.details`

#### Scenario: Machine mutation requires explicit confirmation

- **WHEN** `env remove`, `backup delete`, `db restore`, or `db drop` runs in JSON/TOON mode without `--yes` and without `--dry-run`
- **THEN** no prompt or mutation occurs, stdout contains one `confirmation_required` failure envelope, and the command exits `1`

#### Scenario: Explicit machine mutation executes

- **WHEN** a supported mutating lifecycle leaf is invoked with `--yes` in JSON/TOON mode
- **THEN** the same operation runs once and its result is emitted under the normal renderer-independent exit mapping

#### Scenario: Machine remove requires explicit confirmation

- **WHEN** `odcli env remove ENV --format json`, `--format toon`, or `--json` is invoked without `--yes`
- **THEN** no prompt is rendered, removal is not called, stdout contains one failure envelope with `error.code="confirmation_required"`, and the command exits `1`

#### Scenario: Explicit machine remove executes

- **WHEN** `odcli env remove ENV --yes --format json`, `--format toon`, or `--json` is invoked
- **THEN** the same removal operation runs once and its result is emitted as one document under the normal renderer-independent exit mapping

#### Scenario: Secrets redacted

- **WHEN** an error occurs during checkout or resource lifecycle execution
- **THEN** every machine or Rich error message redacts passwords, config bodies, sensitive environment values, and captured secrets before emission

#### Scenario: Diagnostic machine formats share one result graph

- **WHEN** one frozen diagnostic or resource result is projected as JSON and TOON
- **THEN** both decoded envelopes contain equal typed fields, numeric byte fields, warnings, and completeness state

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
- **THEN** they equal canonical `PUBLIC_LEAF_CASES`, including every backup, database, and resource leaf added by this change
- **AND** no second bounded-leaf table is introduced

#### Scenario: Mutating lifecycle dry-runs are canonical leaves

- **WHEN** the characterization gate exercises `backup delete`, `db restore`, or `db drop` with `--dry-run` in every shared format
- **THEN** each appears exactly once in canonical `PUBLIC_LEAF_CASES` as `mutating-or-spawning` with required dry-run support
- **AND** no file, database, configuration, process, or catalogue mutation occurs

#### Scenario: Database drop is a canonical bounded leaf

- **WHEN** the stable machine-output characterization gate exercises `db drop DATABASE --dry-run` in every shared format
- **THEN** `db drop` appears exactly once in canonical `PUBLIC_LEAF_CASES` as `mutating-or-spawning` with required dry-run support
- **AND** no database, session, or catalogue mutation occurs

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

`odcli run` SHALL launch the resolved Odoo runtime from either a ready environment or an initialized project. For project context, it SHALL derive the Python executable, Odoo entry point, source Odoo config, runtime working directory, preferred HTTP port, default database, default run arguments, and project PostgreSQL binding from `.odcli/project.toml` and the referenced config. Missing required runtime fields or files SHALL fail before process construction with a sanitized actionable error.

The command SHALL preserve the existing literal `--` delimiter rule, exact passthrough argument order, protected runtime-identity validation, free-port preflight, dry-run rendering, inherited native streams, foreground process-group cleanup, and exit-code behavior. Project context has no environment use metadata, so it SHALL NOT call `EnvironmentResource.record_use()`; environment context SHALL retain its existing record-use behavior after successful preflight and before execution.

#### Scenario: Project run needs no runtime path arguments

- **WHEN** `odcli run` executes from an initialized main checkout whose manifest references valid Python, Odoo entry point, and config
- **THEN** it constructs and launches the foreground command without requiring those paths as CLI arguments

#### Scenario: Project defaults and passthrough compose deterministically

- **WHEN** project context defines default run arguments and the caller supplies allowed arguments after `--`
- **THEN** the captured command contains project defaults followed by the exact caller arguments in their original order

#### Scenario: Project dry-run has no side effects

- **WHEN** `odcli run --dry-run` resolves project context
- **THEN** it emits the bounded execution plan without starting Odoo, mutating the environment catalogue, or writing use metadata

#### Scenario: Environment run retains metadata behavior

- **WHEN** `odcli run` resolves a ready environment and the port preflight succeeds
- **THEN** it records environment use exactly once before executing the captured foreground command

#### Scenario: Native process contract is context-independent

- **WHEN** an environment-based or project-based foreground run exits non-zero or is interrupted
- **THEN** native streams are preserved, the actual exit code is returned, and interrupt cleanup returns exit `130`

#### Scenario: Port conflict deterministic error

- **WHEN** `odcli run -- --dev=reload` finds the effective bound port occupied
- **THEN** it returns `port-conflict` with ownership unknown and performs no foreground command construction, use update, config change, or process launch

#### Scenario: Free port starts Odoo

- **WHEN** `odcli run -- --dev=reload --log-level debug --dev=xml` finds the effective port free
- **THEN** it captures `run_foreground_command` once with the exact delimiter arguments and executes that captured command

#### Scenario: Delimiter is required for native arguments

- **WHEN** a caller invokes `odcli run --dev=reload` without the `--` delimiter
- **THEN** Click reports an unknown-option usage error with exit code `2` before SDK resolution or launch

#### Scenario: Bare positional input is rejected

- **WHEN** a caller invokes `odcli run sale` without a literal `--`
- **THEN** the command reports a usage error with exit code `2` and performs no SDK resolution, use update, command construction, or launch

#### Scenario: Protected override is rejected before spawn

- **WHEN** `odcli run -- --database other` or another protected runtime-identity override is invoked
- **THEN** the SDK validator returns a sanitized error before command execution and no child process starts

#### Scenario: Dry-run and execution use one captured argv

- **WHEN** the same allowed delimiter arguments are supplied to dry-run and normal execution under a recording executor
- **THEN** dry-run displays the exact captured foreground step and normal execution consumes it without reconstructing argv

#### Scenario: Native TTY and exit behavior remain unchanged

- **WHEN** `odcli run -- --workers=2` executes normally, exits non-zero, or is interrupted
- **THEN** inherited stdin/stdout/stderr remain native, the real exit code is returned, and interrupt cleanup preserves exit `130`

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
- `eval` SHALL return captured user stdout separately from the expression result, including print-only `exec(...)`, Unicode/multiline output, and output emitted before an exception.
- Eval failure SHALL distinguish Odoo startup failure from user-code failure and preserve the exception type, message, and relevant traceback/source context after bounded truncation; startup-log prefixes SHALL NOT replace the actual exception. A valid framed user-code exception SHALL produce envelope v1 with `ok=false`, no top-level `result` or `data`, `error.code="eval_user_code_failed"`, and `error.details` containing exactly `result=null`, bounded `user_stdout`, non-null structured `user_error`, and boolean `truncated`. A non-zero eval without a valid framed user-code error SHALL use `error.code="eval_startup_failed"` and SHALL NOT fabricate framed details.
- Rich SHALL label user output separately; JSON and TOON SHALL carry it as a structured field and SHALL never inject raw prints into machine stdout.
- `exec` MUST читать explicit file (`-` означает caller stdin), передавать script через shell stdin и устанавливать predictable `sys.argv` из tokens после `--`.
- `exec` SHALL apply the same framed failure shape and exit behavior as `eval`: a valid framed user-code exception SHALL exit `1` with `ok=false`, no top-level `result` or `data`, `error.code="exec_user_code_failed"`, and `error.details` containing exactly `result=null`, bounded `user_stdout`, non-null structured `user_error`, and boolean `truncated`; a non-zero exec without a valid framed user-code error SHALL use `error.code="exec_startup_failed"` and SHALL NOT fabricate `error.details`.
- default MUST быть best-effort shell rollback. Explicit `--commit` MUST быть виден в plan.
- project config MUST NOT автоматически подставлять eval/exec source.
- All output and diagnostics SHALL remain secret-redacted, and failures SHALL remain non-zero.

#### Scenario: Eval expression
- **WHEN** `odcli eval "1+1"` executes in a supported environment or project context
- **THEN** result `2` is returned separately from an empty captured user-output field and no RPC is used

#### Scenario: Print then fail
- **WHEN** eval runs `exec("print('before'); raise ValueError('failure')")`
- **THEN** the command exits `1` and JSON/TOON emit one failure envelope with `ok=false`, no top-level `result` or `data`, and `error.code="eval_user_code_failed"`
- **AND** `error.details.result` is null, `error.details.user_stdout` contains `before`, `error.details.user_error` identifies `ValueError: failure` with relevant source context, and `error.details.truncated` reports bounded truncation
- **AND** Rich renders those same redacted details as separate result, output, and error sections

#### Scenario: Startup failure has no user-code payload
- **WHEN** eval exits non-zero before producing a valid framed user-code error
- **THEN** the command exits `1` with one failure envelope using `error.code="eval_startup_failed"`, a sanitized message, no top-level `result` or `data`, and no fabricated `error.details`

#### Scenario: Exec script prints then fails
- **WHEN** `odcli exec` runs a script that prints `before` and then raises `ValueError("failure")`
- **THEN** the command exits `1` and JSON/TOON emit one failure envelope with `ok=false`, no top-level `result` or `data`, and `error.code="exec_user_code_failed"`
- **AND** `error.details` contains exactly `result=null`, bounded `user_stdout` containing `before`, non-null structured `user_error` identifying `ValueError: failure` with relevant source context, and boolean `truncated`
- **AND** Rich renders those same redacted details as separate result, output, and error sections

#### Scenario: Exec startup failure has no user-code payload
- **WHEN** exec exits non-zero before producing a valid framed user-code error
- **THEN** the command exits `1` with one failure envelope using `error.code="exec_startup_failed"`, a sanitized message, no top-level `result` or `data`, and no fabricated `error.details`

### Requirement: `module` commands

```bash
odcli module list --state installed
odcli module update comerta_base --dry-run
odcli module update comerta_base --yes
odcli module test comerta_base --test-tags /comerta_base --reload-tests
```

- `list [MODULE...]` MUST read `ir.module.module`.
- `update` MUST require `--yes`; before spawning it SHALL reject an empty requested selection and SHALL pass the requested names into the Odoo domain as a real list using the same JSON-decode technique as module listing, not a serialized list string.
- After `button_immediate_upgrade()`, `update` SHALL verify that every requested module appears in the returned `updated` collection; a missing module or empty recordset SHALL be a non-zero failure and SHALL never emit `ok=true`.
- `update` SHALL operate from either a ready environment or initialized project through the shared context and configured database; explicit `--env` SHALL retain precedence.
- `test` MUST remain a backward-compatible alias to the top-level local Odoo test operation and SHALL support both context kinds without fabricating an environment.
- install/uninstall MUST NOT be added. Public `ModuleResource` or `TestResource` MUST NOT exist.

#### Scenario: Module list

- **WHEN** `odcli module list --state installed` executes
- **THEN** installed modules are listed via local Odoo shell

#### Scenario: Requested names remain a list

- **WHEN** `odcli module update sale --yes` builds its Odoo shell source
- **THEN** the search domain receives `['sale']` as a list rather than the string `'["sale"]'`

#### Scenario: Empty recordset is not success

- **WHEN** Odoo returns no upgraded record for a requested module
- **THEN** the command exits non-zero with a sanitized failure and does not emit `ok=true` or claim that module was updated

#### Scenario: Project-context update

- **WHEN** `odcli module update sale --yes` runs from an initialized main checkout without an exact environment
- **THEN** it uses the project runtime and configured database through the existing update command path

#### Scenario: Module test compatibility

- **WHEN** an existing caller invokes `odcli module test comerta_base --test-tags /comerta_base`
- **THEN** the request reaches the same preflight, `OdooTestSpec`, runner, and result path as the top-level command

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

The command SHALL accept the shared `environment | project` context. In project context it SHALL derive Python, Odoo entry point, source config, repository root, and configured database from the initialized project; in environment context it SHALL preserve existing generated-config and recorded-runtime behavior. Default SHALL print one debugpy profile; `--write` SHALL atomically create a missing `.vscode/launch.json` and SHALL NOT rewrite existing JSONC. `--dry-run` SHALL report the intended write without creating directories or files. Rich, JSON, and TOON SHALL use the shared output contract.

#### Scenario: Print profile
- **WHEN** `odcli vscode generate` runs from a complete initialized project with no exact environment
- **THEN** one profile derived from project values is printed and no file is written

#### Scenario: Existing JSONC is preserved
- **WHEN** `--write` targets an existing `.vscode/launch.json`
- **THEN** the command refuses to overwrite it in either context

### Requirement: Instance commands share one ready path

Project-capable instance commands MUST obtain the client, resolved `environment | project` context, and `OdooInstance` through one shared internal path. Command bodies MUST NOT duplicate context precedence, runtime verification, instance construction, or client construction. Environment-only commands MUST narrow the shared result explicitly and reject project context.

Port preflight remains specific to `run`.

#### Scenario: Eval and run share resolve

- **WHEN** `odcli eval 1` and `odcli run` execute under the same supported context
- **THEN** both resolve that context through the shared path rather than command-specific helpers

#### Scenario: Lifecycle remains environment-only

- **WHEN** an environment lifecycle operation is invoked from only the main project checkout without an environment selector
- **THEN** it does not treat the project as a development environment

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

Every success machine result SHALL contain `owner_kind: "environment" | "project"`, canonical `project_id`, nullable `environment_id` and `environment_name`, `worktree_root`, `database`, `http_url`, `command_prefix: list[str]`, selector kind/value and provenance, modules, and exit code. For an environment owner both environment fields SHALL identify the resolved environment; for a project owner both SHALL be null. All common worktree/runtime fields SHALL describe the same resolved owner and SHALL NOT fabricate an environment. An executed result SHALL additionally contain effective native test tags, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags from `OdooTestResult`. A successful `--changed --dry-run` result SHALL instead contain `dry_run=true` plus complete base/Git provenance and SHALL omit `test_tags`, `reload_tests`, `allow_empty`, counts, and failure/zero-tests flags. A successful changed selection with no addons SHALL contain `reason="no_addon_changes"`, complete base/Git provenance, empty modules, and `exit_code=0`, and SHALL omit those same execution-only fields; if it is also a dry-run it MAY additionally contain `dry_run=true`. Neither non-executed state SHALL construct or imply an `OdooTestResult`, fabricate zero counts/false flags, or emit execution progress. JSON and strict-decoded TOON SHALL be semantically equal in all three states. Raw sanitized Odoo diagnostics SHALL be written only to stderr; machine stdout SHALL contain exactly one document without ANSI, prompts, progress, or embedded raw logs.

Rich output SHALL show the same owner identity, project, nullable environment identity, common worktree/runtime context, selection/modules, and exit status. It SHALL show final counts only for executed results; `--changed --dry-run` and `no_addon_changes` MAY use a Rich table but SHALL not display fabricated counts or execution progress.

#### Scenario: JSON and TOON test parity

- **WHEN** equivalent frozen test results are emitted with `--format json` and `--format toon`
- **THEN** decoded envelope-v1 values are equal, stdout contains one document, diagnostics are only on stderr, and both commands return the typed result's exit code

#### Scenario: Changed no-op is a successful document

- **WHEN** `odcli test --changed --format json` finds only docs/non-addon paths
- **THEN** stdout contains one success envelope with `reason="no_addon_changes"`, selected modules is empty, and no Odoo diagnostics/process are produced

#### Scenario: Dry-run machine shape has no execution fields

- **WHEN** `odcli test --changed --dry-run --format json` safely selects one or more addons
- **THEN** stdout contains selection/base provenance, modules, `dry_run=true`, and `exit_code=0`, omits tags/options/counts/failure/zero flags, and produces no execution progress or Odoo diagnostics

#### Scenario: Environment and project owner shapes are explicit

- **WHEN** equivalent executed tests resolve once to an environment and once to its initialized project
- **THEN** every format reports `owner_kind`, the same canonical project/common runtime fields, non-null environment identity only for the environment owner, and null environment identity only for the project owner

#### Scenario: Owner fields have parity in non-executed states

- **WHEN** changed selection returns a no-addon no-op or dry-run for either owner kind
- **THEN** JSON, strict-decoded TOON, and Rich expose the same owner/common context while execution-only fields and progress remain absent

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
odcli db drop DATABASE [--force-default] [--force-connections] [--yes] [--dry-run]
```

`commands/db.py` SHALL parse options, resolve project/environment context, call existing public resources for refresh/password reset, call the CLI-private cluster-bound PostgreSQL operation for guarded drop, render typed results, and map typed exceptions. The guarded drop path SHALL NOT call, replace, or change the public Odoo HTTP `DatabaseResource.drop/drop_command` methods. It SHALL not download, restore, acquire locks, run ORM scripts, edit manifests, or construct alternate result dictionaries itself. The group SHALL be registered through the stable `odoo_instance_sdk.cli:cli` entry point after rebasing the MYL-55 CLI foundation.

#### Scenario: Help exposes database commands

- **WHEN** `odcli db --help` runs
- **THEN** it lists `refresh`, `reset-admin-password`, and `drop` with the documented options

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

### Requirement: PostgreSQL CLI leaves use current project and database context

The CLI SHALL expose `odcli db locks [DATABASE]`, `odcli db stats [DATABASE]`, `odcli db bloat [DATABASE]`, `odcli db init-monitoring [DATABASE] --yes`, root `odcli psql [PSQL_ARGS...]`, and the enriched existing `odcli postgres status`. These leaves SHALL reuse current cwd/project/environment resolution and SHALL NOT add a PostgreSQL-specific `--env`, host, port, user, password, or database-connection selector.

In a registered worktree, omitted `DATABASE` SHALL use the generated environment database. In a project root, omission SHALL work only for an unambiguous project default. An explicit database SHALL stay within the resolved cluster. Diagnostics SHALL work while Odoo is stopped if PostgreSQL is available.

#### Scenario: Worktree omission uses generated database

- **WHEN** `odcli db locks` runs in a registered worktree whose generated config binds database `feature_42`
- **THEN** the command queries `feature_42` on that worktree's resolved project cluster

#### Scenario: No PostgreSQL environment selector

- **WHEN** help is rendered for diagnostics and `psql`
- **THEN** no new PostgreSQL-specific environment or connection-identity selector is offered

### Requirement: Native psql is a raw passthrough command

`odcli psql` SHALL accept, without a mandatory `--`, exactly the closed zero-value and one-value non-identity option set specified by `database-management`, pass those tokens with exact boundaries to `DatabaseResource.psql_command()`, inherit stdin/stdout/stderr and TTY, and return the native exit code. It is a bounded native-option passthrough: protected connection aliases, positional database/user/connection strings, unknown options, missing option values, and operands after `--` SHALL fail before spawn. It SHALL accept neither `--format` nor `--json`.

Running `odcli psql` is the explicit trust boundary for potentially mutating SQL; the CLI SHALL NOT add a per-statement confirmation, custom REPL, Rich wrapper, or machine envelope. Missing binary, unreachable cluster, missing/ambiguous database, and protected connection flags SHALL produce short sanitized actionable errors before spawn.

#### Scenario: Interactive psql inherits the terminal

- **WHEN** `odcli psql` runs with no native arguments in a valid TTY context
- **THEN** native psql owns the terminal streams, completion/history/signals work, and its exit code becomes the CLI exit code

#### Scenario: One-shot native query passes through

- **WHEN** `odcli psql -c 'SELECT current_database();'` runs
- **THEN** psql receives the exact `-c` argument plus SDK-bound connection identity and its native stdout/stderr/exit code are preserved

#### Scenario: Connection override fails before spawn

- **WHEN** `odcli psql --dbname other` or an equivalent protected option is supplied
- **THEN** Click exits with an actionable usage error and no psql process starts

#### Scenario: Positional identity fails before spawn

- **WHEN** `odcli psql other_db`, `odcli psql postgresql://other/db`, `odcli psql -- other_db`, or a second native positional username is supplied
- **THEN** Click exits with an actionable usage error and no psql process starts

#### Scenario: Declared value-taking options do not become positional identity

- **WHEN** `odcli psql -c 'SELECT 1' -f query.sql -v ON_ERROR_STOP=1 -F '|' -Pborder=2 --record-separator=:: -T class=compact` is supplied
- **THEN** token-aware validation consumes each option value at its native arity and preserves every boundary in the planned argv

#### Scenario: Unknown native option fails closed

- **WHEN** `odcli psql` receives an option not listed in the supported grammar
- **THEN** Click exits with an actionable usage error and no psql process starts

### Requirement: Monitoring initialization uses shared confirmation and dry-run

`odcli db init-monitoring [DATABASE]` SHALL be mutating and SHALL require `--yes` for execution in every noninteractive/machine context. Without `--yes`, machine modes SHALL emit one `confirmation_required` failure document without prompting or mutation; Rich interactive mode MAY use the existing confirmation behavior. `--dry-run` SHALL build and render the exact shared command plan without prompt, process spawn, or database mutation, and SHALL not require `--yes`.

Pure read-only `db locks`, `db stats`, `db bloat`, and `postgres status` SHALL NOT add a redundant `--dry-run`. `odcli psql --dry-run ...` SHALL be supported by the shared native command-plan option while normal `psql` remains raw passthrough.

#### Scenario: Machine initialization requires yes

- **WHEN** `odcli db init-monitoring --format json` is invoked without `--yes`
- **THEN** no prompt or PostgreSQL process occurs and one failure envelope reports `confirmation_required`

#### Scenario: Initialization dry-run is exact and inert

- **WHEN** `odcli db init-monitoring --dry-run` is invoked for a valid SDK-owned database
- **THEN** the exact sanitized process/action plan is rendered and no extension is created

#### Scenario: Psql dry-run preserves planned native argv

- **WHEN** `odcli psql --dry-run -c 'SELECT 1'` is invoked
- **THEN** the shared plan shows the exact sanitized native psql step that normal execution would consume and does not spawn it

### Requirement: Project restore postconditions use the database authority

When `odcli db refresh --restore` targets a project PostgreSQL cluster, existence checks before and after restore MUST query that PostgreSQL endpoint directly when a PostgreSQL probe is available. The checks MUST NOT infer absence solely from the running Odoo database-manager list because an Odoo process constrained by `--database` can omit a newly restored database. An inconclusive PostgreSQL probe MUST fail closed rather than silently converting the result into confirmed absence.

#### Scenario: Running Odoo is restricted to the previous database

- **WHEN** restore creates the target in the project PostgreSQL cluster but `/web/database/list` only returns the database selected when Odoo started
- **THEN** the post-restore check confirms the target through PostgreSQL and the refresh proceeds to its remaining steps

#### Scenario: Direct probe confirms absence

- **WHEN** the planned PostgreSQL post-restore probe completes successfully with no matching database
- **THEN** restore fails with the retained-backup and retained-database safety context

### Requirement: Rich Click help and validation errors

The root command and every nested group and command SHALL use `rich-click>=1.9,<2` for Click-generated help, usage, and validation errors. Every visible entry SHALL have a useful one-line description; existing option types, metavars, defaults, required markers, choices, ranges, command names, parsing, exit codes, completion behavior, and `--help` behavior SHALL remain intact. Help MAY use at most four stable task-oriented panels and SHALL leave small pages ungrouped. Command results, output envelopes, passthrough streams, logs, and progress SHALL continue through their existing boundaries and SHALL NOT be rendered by `rich-click`.

#### Scenario: Typed leaf help remains informative
- **WHEN** a typed nested leaf is rendered at a narrow terminal width
- **THEN** its description, required/default/type metadata, and options remain readable without changing parsing

#### Scenario: Redirected help has no ANSI
- **WHEN** help or a Click validation error is redirected or color is disabled
- **THEN** the output is readable, contains no ANSI escapes, and retains Click's exit code

### Requirement: Shared human dry-run projection

Default Rich dry-run output SHALL use one shared projection showing the command goal, resolved project/environment/database/modules, intended mutations, preconditions, warnings, and the exact sanitized `display` of every captured `ProcessStep` in plan order. It SHALL collapse implementation-only probes and omit repeated classifications, executable, cwd, timeout, environment, stdin, and fingerprint details, but semantic summaries SHALL NOT replace or hide real subprocess commands. Argument boundaries and secret redaction SHALL come from the immutable process-step projection. JSON and TOON SHALL retain the complete immutable plan and remain semantically equal.

#### Scenario: Human plan is decision-oriented and executable-aware

- **WHEN** a mutating command with process steps is invoked with `--dry-run` in default Rich mode
- **THEN** the user sees targets, mutations, preconditions, warnings, and every exact sanitized process display without the remaining low-level fields

#### Scenario: Human plan is decision-oriented

- **WHEN** a mutating command is invoked with `--dry-run` in default Rich mode
- **THEN** the user sees targets, mutations, preconditions, and warnings without low-level execution fields

#### Scenario: Confirmed dry-run command families are visible

- **WHEN** Rich dry-run is invoked for `run`, `env checkout`, `postgres up`, `postgres stop`, `module update`, or `translations export` and its immutable plan contains a process step
- **THEN** output is non-empty and contains every such process display even when a precondition fails or a command-specific summary exists

#### Scenario: Conditional process branches are visible

- **WHEN** recording or fake execution proves that `env sync`, `env remove`, `db drop`, `test`, or a project/environment run variant contains a conditional process step
- **THEN** the same Rich contract shows that process display and does not replace it with only a semantic summary

#### Scenario: Machine plan remains complete

- **WHEN** the same dry-run is emitted as JSON or TOON
- **THEN** the full immutable execution snapshot, including exact redacted process details and fingerprint, is present and both decoded documents are equal

### Requirement: Dry-run reports failed runtime preconditions

`odcli run --dry-run` SHALL finish plan construction when the effective HTTP port is occupied and represent the conflict as a failed precondition or warning without spawning Odoo or changing state. Normal `odcli run` SHALL continue to fail before spawn.

#### Scenario: Occupied port is visible in dry-run
- **WHEN** `odcli run --dry-run` resolves an occupied configured HTTP port
- **THEN** it emits a side-effect-free plan identifying the failed port precondition instead of returning before the plan

### Requirement: Restore progress and command streams

Rich `odcli db refresh --restore` SHALL show logical step progress. On an interactive TTY it SHALL use live current/completed-step rendering; on non-TTY Rich output it SHALL emit deterministic step-prefixed sanitized lines without `Live` or cursor control. An explicit `--show-command-output` SHALL stream sanitized, step-prefixed stdout/stderr only in Rich mode. Combining `--show-command-output` with `--format json`, `--format toon`, or `--json` SHALL be a Click usage error with exit code `2` before SDK work. JSON and TOON without the flag SHALL emit one deterministic final document without Rich rendering or raw stream injection. Existing execution exit codes, captured subprocess results, and redaction SHALL remain unchanged.

#### Scenario: Interactive restore shows plan progress
- **WHEN** restore runs in an interactive Rich terminal without the stream flag
- **THEN** current and completed logical steps are displayed without dumping subprocess output

#### Scenario: Machine restore remains bounded
- **WHEN** restore runs in JSON or TOON mode
- **THEN** stdout contains exactly one parseable document and no live progress or raw command stream

#### Scenario: Stream flag is Rich-only
- **WHEN** `--show-command-output` is combined with JSON, TOON, or the JSON alias
- **THEN** Click exits `2` before restore planning/execution and emits no partial machine document

#### Scenario: Redirected Rich output is line-oriented
- **WHEN** Rich restore output is not attached to a TTY
- **THEN** progress and enabled command streams use sanitized step-prefixed lines without `Live`, ANSI cursor control, or unassociated chunks

### Requirement: Safe database-drop command

The CLI SHALL expose `odcli db drop DATABASE [--force-default] [--force-connections] [--yes] [--dry-run]`. It SHALL require an exact database name, resolve only the current project PostgreSQL cluster, reject system/template databases, display the cluster and database before mutation, require interactive Rich confirmation by default, and require `--yes` for machine execution. JSON, TOON, and the `--json` alias SHALL always be noninteractive and SHALL never call `click.confirm`. A normal machine-mode drop without `--yes` SHALL perform zero SDK/transport/catalogue work, emit exactly one sanitized CLI envelope v1 with `error.code="confirmation_required"`, and exit `1`; with `--yes` it SHALL execute through the normal renderer-independent path. Dry-run in every format SHALL require neither confirmation nor `--yes` and SHALL remain side-effect-free. Dropping the configured project default SHALL additionally require `--force-default`; terminating active sessions SHALL additionally require `--force-connections`. Rich, JSON, and TOON SHALL otherwise use the shared output and confirmation contracts.

#### Scenario: Protected default database is refused
- **WHEN** the exact target is the configured project default and `--force-default` is absent
- **THEN** the command fails before termination or drop and identifies the protection

#### Scenario: Dry-run needs no confirmation
- **WHEN** a valid drop target is invoked with `--dry-run` without force or yes flags unrelated to observed conditions
- **THEN** the command emits the resolved guarded plan without prompt, connection termination, database mutation, or catalogue write

#### Scenario: Machine drop requires explicit confirmation
- **WHEN** normal `db drop` is invoked with JSON, TOON, or `--json` without `--yes`
- **THEN** no prompt or SDK work occurs, stdout contains exactly one sanitized `confirmation_required` envelope, and the command exits `1`

#### Scenario: Explicit machine confirmation executes
- **WHEN** normal `db drop` is invoked in a machine format with `--yes` and all safety preconditions pass
- **THEN** the guarded operation executes once and emits exactly one success or failure envelope under the normal exit mapping

### Requirement: Dependency verification uses the selected Python correctly

`deps verify` SHALL run distribution validation through the configured `uv_executable`. For an explicit Python filesystem path it SHALL execute `uv pip check --python <path>` and SHALL NOT ask that interpreter to execute `pip` as a script. For an existing uv Python selector it SHALL use a valid selector-aware uv invocation. The command SHALL succeed only when `pip_check_ok=true` and `missing_imports` is empty, and SHALL expose concrete distribution diagnostics and missing module/import pairs without installing packages.

#### Scenario: Explicit virtual environment path

- **WHEN** project resolution selects an explicit virtual-environment Python path
- **THEN** the captured distribution step starts with the configured uv executable and contains `pip check --python <exact-path>`

#### Scenario: Distribution conflict

- **WHEN** uv pip check exits non-zero and import probes succeed
- **THEN** Rich, JSON, and TOON report failure, include sanitized distribution diagnostics, and the CLI exits 1

#### Scenario: Missing import

- **WHEN** pip check succeeds but a declared external import probe fails
- **THEN** the result identifies the module/import, the envelope has `ok=false`, and the CLI exits 1

#### Scenario: Dependency verification succeeds

- **WHEN** pip check and every import probe succeed
- **THEN** all formats report `pip_check_ok=true`, no missing imports, `ok=true`, and exit 0

### Requirement: Truthful progress for long bounded commands

Normal Rich execution SHALL use the shared observer for `env checkout`, `env sync`, `test`, `module test`, `module update`, `exec`, `eval`, `translations export`, `db refresh --restore`, `db restore`, `postgres up`, and `postgres approve-image`. It SHALL show the current logical step, completed steps, and elapsed time until termination. It SHALL use a spinner/status when duration is unknown and SHALL show percentage only from a trustworthy total. Non-TTY Rich SHALL emit sparse deterministic lines; machine modes and dry-run SHALL emit no runtime progress. Fast read-only commands SHALL remain excluded unless measured evidence classifies them as long.

#### Scenario: Controlled slow step

- **WHEN** a fake long step pauses after its started event
- **THEN** Rich output is observable before completion and later closes the step with elapsed time

#### Scenario: Logical steps are not time percentages

- **WHEN** a command completes two of five planned steps but has no reliable duration total
- **THEN** output may report step counts but does not label the command 40 percent complete

#### Scenario: Progress command fails

- **WHEN** a long bounded command raises or returns failure
- **THEN** the renderer closes, the active step is failed, and existing error/exit semantics remain authoritative

### Requirement: Concise Rich completion line

After a successful bounded command finishes in Rich mode, the CLI SHALL emit exactly one terminal completion line derived from the existing `OutputDocument`. The line SHALL include `status=success` and only applicable fields among `database`, `url`, `backup`, and `modules`; successful `exec` SHALL additionally include `transaction=rollback|commit`. It SHALL not introduce a second result model or command-specific completion formatter. JSON/TOON and native streaming commands SHALL remain unchanged.

#### Scenario: Exec commits successfully

- **WHEN** bounded `exec` succeeds with commit requested
- **THEN** its final Rich completion line contains `status=success transaction=commit` and only applicable summary fields

#### Scenario: Command has no optional summary field

- **WHEN** a bounded command succeeds without database, URL, backup, modules, or transaction data
- **THEN** its final Rich completion line is exactly the common success status without empty placeholders

#### Scenario: Machine output is unchanged

- **WHEN** the same bounded success is emitted as JSON or TOON
- **THEN** stdout remains the one existing envelope and contains no additional completion line

### Requirement: Backup lifecycle CLI

`odcli backup list [--source URL] [--database NAME] [--all] [--limit N] [--cursor CURSOR]`, `backup show <BACKUP_UUID>`, and `backup validate <BACKUP_UUID>` SHALL be read-only bounded leaves usable without running Odoo or requiring a worktree. List SHALL show full UUID, source database/base URL, catalogue time, format, recorded size, state, and actual file presence; show SHALL add sanitized path, checksum, branch, history, and restore/environment relationships. Validate SHALL distinguish an unavailable validator from an invalid archive. `backup delete <BACKUP_UUID> [--dry-run] [--yes]` SHALL use the exact UUID deletion command and its confirmation contract.

#### Scenario: Default backup list

- **WHEN** `backup list` runs without `--all`
- **THEN** it deterministically returns only available catalogue records and separately reports actual file presence

#### Scenario: Backup details

- **WHEN** `backup show` receives a known UUID
- **THEN** every format represents the same record, audit history, and known relationships without starting Odoo

#### Scenario: Validator is unavailable

- **WHEN** dump validation requires `pg_restore` and it is not available
- **THEN** the command reports validation unavailable rather than labelling the archive corrupt

#### Scenario: Backup deletion preview

- **WHEN** `backup delete UUID --dry-run` executes
- **THEN** the plan identifies the exact file, recorded size, state, and relationships without prompting or mutation

### Requirement: Database inventory and registered restore CLI

`odcli db list [--tracked]` SHALL project the read-only project-cluster inventory in all bounded formats. `odcli db restore <BACKUP_UUID> [--target DATABASE] [--reset-admin-password] [--dry-run] [--yes]` SHALL invoke the registered-local-backup preparation path; normal Rich execution SHALL confirm unless `--yes`, machine execution SHALL require `--yes`, and dry-run SHALL not prompt. A successful restore SHALL switch the project default only after all restore postconditions and optional administrator reset succeed.

#### Scenario: Database list is read-only

- **WHEN** `db list` encounters a PostgreSQL database absent from catalogue provenance
- **THEN** it reports unknown origin and writes no dropped audit event

#### Scenario: Tracked list

- **WHEN** `db list --tracked` runs
- **THEN** it returns only exact cluster/database identities having proven restore or lifecycle relationships

#### Scenario: Restore default target

- **WHEN** `db restore UUID --yes` omits `--target`
- **THEN** it restores to a generated collision-free name, preserves the backup and prior database, and switches default after full success

#### Scenario: Restore preflight fails

- **WHEN** UUID, file, checksum, format, cluster binding, or target-name preflight fails
- **THEN** the command exits 1 with no database or project-config mutation

### Requirement: Resource inspection CLI

`odcli resource list` and `odcli resource doctor` SHALL be bounded read-only leaves available from initialized project context and SHALL project the `local-resource-lifecycle` results through Rich, JSON, and TOON. They SHALL never prompt, delete, repair, register, or reconcile lifecycle state.

#### Scenario: Resource list machine parity

- **WHEN** one frozen resource inventory is emitted as JSON and TOON
- **THEN** decoded envelopes are equal and distinguish logical bytes, measured bytes, completeness, ownership, active use, and reclaimability

#### Scenario: Doctor finds leftovers

- **WHEN** doctor detects missing catalogue files, owned `.part` leftovers, unknown owned-directory files, filestore uncertainty, or cleanup-failed environments
- **THEN** Rich output gives sanitized actionable findings and machine output contains the same typed findings without changing them

### Requirement: CLI init tests use an isolated catalogue

All tests that invoke `odcli init` SHALL replace the `get_catalog_path()` symbol used by the CLI and use controlled worker-local XDG/data roots and sentinel catalogue paths through one shared fixture. Parallel full-suite execution SHALL not resolve, open, read, write, or modify the production-resolved user catalogue path, and monitor tests SHALL not observe projects registered by another test worker.

#### Scenario: Parallel init suite preserves user data

- **WHEN** the full init-related suite runs in parallel under a spy that rejects access to the production-resolved catalogue path
- **THEN** every catalogue open/read/write targets a controlled worker-local sentinel path and the production path is never opened or snapshotted

#### Scenario: Worker catalogues are isolated

- **WHEN** two test workers initialize different temporary projects
- **THEN** each worker's monitor fixtures see only their own temporary catalogue records

### Requirement: Project-owned catalogue scope

Commands that list catalogue records carrying project ownership SHALL resolve the current project by default and return only records owned by that project. `backup list` and `resource list` SHALL follow the existing `env list` project-selection pattern; global output SHALL require explicit `--all-projects`. Outside any project, the command SHALL require `--all-projects` rather than silently selecting a global page. Already project-bound commands such as `db list` SHALL remain project-bound and SHALL not gain a redundant scope switch. Filters, pagination, and read-only behavior SHALL remain unchanged. Machine schemas SHALL remain unchanged except for the additive CLI `env list.worktree_path` field and the new CLI `env path` envelope defined by CLI worktree path access.

#### Scenario: Backup list defaults to current project

- **WHEN** `backup list` runs inside an initialized project without `--all-projects`
- **THEN** every returned record belongs to the resolved project and unrelated catalogue records are absent

#### Scenario: Resource list defaults to current project

- **WHEN** `resource list` runs inside an initialized project without `--all-projects`
- **THEN** its inventory is limited to the resolved project using the existing ownership identities

#### Scenario: Global catalogue output is explicit

- **WHEN** an owned catalogue list is run with `--all-projects`
- **THEN** it may return records from all projects and its provenance reports the explicit cross-project selection

#### Scenario: Outside project fails without global opt-in

- **WHEN** `backup list` or `resource list` runs outside project context without `--all-projects`
- **THEN** it exits actionably without returning a global catalogue page

### Requirement: Rich list presentation contract

Every CLI command named `list` SHALL render its bounded Rich result as exactly one readable Rich table per logical result set, with stable title/heading and columns appropriate to its records. The audited inventory SHALL include `env list`, `backup list`, `db list`, `resource list`, and `module list`. Filesystem or storage byte counts SHALL use the existing shared human-readable byte formatter in Rich mode. `db list` SHALL show its bound cluster once in the title or heading and columns for database, size, sessions, default, and origin. JSON and TOON SHALL preserve exact integer byte values and existing list schemas except for the single approved additive change to an existing list schema: CLI `env list` SHALL add `worktree_path` as defined by CLI worktree path access. Every other list schema SHALL remain unchanged. The separately approved new `env path` leaf SHALL use its new envelope contract and SHALL NOT alter another leaf's schema.

#### Scenario: Backup list has one table

- **WHEN** one Rich `backup list` invocation returns records
- **THEN** it emits exactly one table and every byte-size cell is human-readable

#### Scenario: Database list does not repeat cluster

- **WHEN** Rich `db list` returns multiple databases from one bound cluster
- **THEN** the cluster appears once and each database occupies one row with database, human size, sessions, default, and origin columns

#### Scenario: All list families follow one contract

- **WHEN** the parameterized presentation inventory renders representative `env`, `backup`, `db`, `resource`, and `module` list results
- **THEN** each Rich result is tabular, non-duplicated, and uses human sizes wherever it displays filesystem or storage bytes

#### Scenario: Machine bytes remain exact

- **WHEN** the same list result is emitted as JSON and TOON
- **THEN** byte fields remain exact integers and strict-decoded documents are semantically equal; `env list` differs only by its approved additive `worktree_path`, while every other existing list schema is unchanged and the new `env path` envelope changes no list schema

### Requirement: Project-bound HTTP endpoint precedence

Every project-bound command that resolves the local Odoo endpoint SHALL apply one rule: an explicit command override first, then `ProjectConfig.preferred_http_port`, then `http_port` from the effective generated or source Odoo config, then the existing Odoo default only when neither configured value exists. The selected interface SHALL retain existing safe local-interface rules. Commands SHALL share the existing project runtime resolution path and SHALL NOT add another port setting or configuration abstraction.

#### Scenario: Preferred project port wins for restore

- **WHEN** `project.toml` specifies `preferred_http_port=8068`, the Odoo config omits `http_port`, and `db restore` performs project-bound postconditions
- **THEN** it uses the local Odoo endpoint on port 8068 rather than defaulting to 8069

#### Scenario: Explicit override wins

- **WHEN** an affected command accepts and receives an explicit endpoint or port override while the project also has a preferred port
- **THEN** the explicit value is used

#### Scenario: Odoo config remains fallback

- **WHEN** no explicit override and no preferred project port exist but the effective Odoo config contains `http_port`
- **THEN** every affected project-bound command uses that config port

#### Scenario: Affected commands share precedence

- **WHEN** a parameterized contract exercises every CLI path that reads `http_port` or infers a project-local Odoo endpoint
- **THEN** all paths resolve the same effective endpoint for equivalent inputs

### Requirement: CLI worktree path access

`odcli env list` SHALL add a `WORKTREE` column in Rich output and a CLI-only `worktree_path` field for every environment result in JSON and TOON. This field SHALL be the sole approved additive change to the existing `env list` machine schema. Values SHALL come from the existing catalogue `DevelopmentEnvironment.worktree_path` joined by stable environment ID, not directory-name inference. This CLI enrichment SHALL not add the path to canonical monitor, FastAPI, or dashboard snapshots and SHALL perform no second metrics collection.

The CLI SHALL add read-only `odcli env path [ENVIRONMENT]`. From inside an exact registered worktree, omission of the selector SHALL resolve that environment. From a project root or elsewhere, the positional selector SHALL use the existing environment name-or-UUID resolver. Unknown, ambiguous, removed, missing, or non-directory worktrees SHALL fail actionably; no arbitrary environment SHALL be selected. Root `--env` SHALL not become a command-local substitute.

Rich/default success SHALL write exactly one plain absolute path followed by one newline, with no label, table, ANSI, progress, or completion summary, so command substitution works even when the path contains spaces. JSON and TOON SHALL emit the approved new ordinary single-document `env path` envelope whose `result` contains exactly `environment_id`, `name`, and `worktree_path`; the existing standard envelope fields remain required, and this new leaf contract SHALL NOT modify any existing machine payload or schema. The CLI SHALL not add `env cd` or a nested shell.

#### Scenario: Path from registered worktree

- **WHEN** `odcli env path` runs anywhere inside a registered environment worktree
- **THEN** stdout is exactly that environment's absolute stored path plus a newline

#### Scenario: Positional name and UUID selection

- **WHEN** `env path` receives an unambiguous existing environment name or UUID from the project root
- **THEN** both selectors resolve the same stored path without guessing storage layout

#### Scenario: Invalid environment is not guessed

- **WHEN** selection is unknown, ambiguous, removed, or has a missing/non-directory worktree
- **THEN** the command exits non-zero with an actionable sanitized error and emits no path

#### Scenario: Path with spaces supports shell command substitution

- **WHEN** the stored absolute worktree path contains spaces and Rich/default `env path` succeeds
- **THEN** it emits the path without quoting or ANSI so `cd "$(odcli env path <environment>)"` selects the directory

#### Scenario: Machine path parity

- **WHEN** `env path` succeeds in JSON and TOON modes
- **THEN** each emits one standard new-leaf envelope whose `result` contains exactly equal `environment_id`, `name`, and absolute `worktree_path`, and no existing leaf schema changes

#### Scenario: Help documents normal shell flow

- **WHEN** the user reads `env path --help` or the short README example
- **THEN** it shows path retrieval and ordinary shell `cd` without advertising an `env cd` command

### Requirement: Human-oriented Rich bounded output

Every bounded CLI leaf SHALL render Rich output as a human-oriented summary using an appropriate table, labelled panel, concise sentence, and spacing for distinct sections. Rich SHALL NOT use raw JSON, concatenated structured documents, or unstructured key-value dumps as the primary result. Genuinely nested details MAY include contained pretty-printed JSON only when converting them would hide useful structure. JSON and TOON SHALL preserve their complete existing payloads, schemas, and single-document parity except for exactly two approved CLI-only worktree contracts: additive `worktree_path` in each `env list` environment result and the new `env path` envelope whose `result` contains `environment_id`, `name`, and `worktree_path`. Every other bounded-leaf machine payload and schema SHALL remain unchanged. Existing shared emitters and command-local renderers SHALL be corrected in place; no parallel renderer framework SHALL be introduced.

#### Scenario: Restore result is a human summary

- **WHEN** bounded Rich `db restore` completes
- **THEN** its primary result is a concise labelled summary or panel rather than a JSON-shaped payload

#### Scenario: Structured families remain separated

- **WHEN** a Rich result contains a summary plus nested details or multiple logical sections
- **THEN** labels, indentation, and whitespace make the sections distinct without concatenated documents

#### Scenario: Presentation inventory covers bounded CLI

- **WHEN** the shared parameterized presentation contract renders every bounded command family
- **THEN** none has empty output, raw primary JSON, concatenated documents, or an unstructured key-value dump

#### Scenario: Machine documents are unchanged

- **WHEN** the same results are emitted in JSON and TOON
- **THEN** complete structured payloads, schemas, exit semantics, and decoded parity remain unchanged except for the additive `env list.worktree_path` field and the new `env path` envelope; no other machine contract changes

### Requirement: Canonical resource command aliases

The CLI SHALL expose the following canonical names while retaining each existing name as a compatible alias of the same Click command object: `env create`/`env checkout`, `env ls`/`env list`, `env rm`/`env remove`, `backup ls`/`backup list`, `backup inspect`/`backup show`, `backup rm`/`backup delete`, `db ls`/`db list`, `db rm`/`db drop`, `postgres ps`/`postgres status`, `resource ls`/`resource list`, and `module ls`/`module list`. Each pair SHALL share callback, parameters, validation, confirmation, immutable plan, stdout, stderr, exit code, dry-run behavior and the existing stable machine `command` identifier. Help SHALL present one operation with its alternate spelling rather than two independent operations. `postgres up`, `postgres stop`, domain-specific verbs, and existing top-level concise verbs SHALL remain unchanged.

#### Scenario: Alias pair has identical observable behavior

- **WHEN** either spelling in a canonical/compatible pair receives the same arguments and input
- **THEN** both invocations produce semantically identical Rich, JSON and TOON results, errors, plans, prompts and exit codes

#### Scenario: Existing script keeps working

- **WHEN** a script invokes an existing compatible spelling after the aliases are added
- **THEN** validation and the stable machine `command` identifier remain unchanged

#### Scenario: Destructive aliases preserve safety

- **WHEN** `env rm`, `backup rm`, or `db rm` is invoked without the confirmation required by its existing command
- **THEN** the alias enforces the same `--yes`, prompt, dry-run and machine-mode refusal contract as the compatible spelling

### Requirement: Replace a selected isolated environment backup

`odcli db restore BACKUP_UUID --replace` SHALL resolve exactly one registered environment from cwd or the existing root `--env` selector and SHALL accept only a non-removed `db_mode=copy` environment with an exact recorded target database. It SHALL reject project context, shared environments, ambiguous selectors, mismatched generated configuration/catalogue data, unavailable or invalid backup artifacts, a live owned runtime, active target-database sessions, and `--target` before mutation. Because no explicit force contract exists, replacement SHALL NOT terminate active sessions. The existing `--reset-admin-password` option SHALL retain its restore compatibility and SHALL complete before final replacement provenance is committed. Rich confirmation, machine-mode `--yes`, dry-run, redaction, progress, output and exit codes SHALL use the existing restore contracts; no command-local environment selector SHALL be added.

#### Scenario: Cwd environment replacement

- **WHEN** the cwd belongs to a stopped registered COPY environment and an available exact backup UUID passes preflight
- **THEN** `db restore BACKUP_UUID --replace` restores that environment through the replacement contract without changing its identity or target database name

#### Scenario: Explicit environment replacement

- **WHEN** `odcli --env ENVIRONMENT db restore BACKUP_UUID --replace` is invoked outside the worktree
- **THEN** root context resolution selects the same environment and produces the same behavior as cwd resolution

#### Scenario: Unsafe context is rejected

- **WHEN** replacement resolves project context, a shared/removed environment, mismatched records, or a running owned process
- **THEN** it fails before database, filestore, catalogue, config, or process mutation

#### Scenario: Active database sessions fail closed

- **WHEN** replacement preflight or execution revalidation observes any active session on the exact target database
- **THEN** it fails before mutation and does not terminate any session

#### Scenario: Replacement target cannot be overridden

- **WHEN** `db restore BACKUP_UUID --replace --target OTHER_DATABASE` is invoked
- **THEN** option validation rejects the invocation before mutation because replacement must use the environment's exact recorded target

#### Scenario: Admin-password reset remains compatible

- **WHEN** replacement is invoked with `--reset-admin-password` and restore reaches that existing post-restore operation
- **THEN** the reset completes before final provenance is committed, and a reset failure follows replacement compensation without advertising the selected backup as active

### Requirement: Stop a selected environment runtime

Top-level `odcli stop` SHALL resolve exactly one registered environment from cwd or the existing root `--env` selector. Without adding a runtime migration, it SHALL re-read the runtime row's environment owner and PID/create time plus the environment row's `runtime_json` (`odoo_bin`, `runtime_cwd`) and generated-config path. At execution it SHALL terminate only when the live PID create time, executable, argv, cwd and config argument match those records and, on POSIX, the live process satisfies `pgid == pid`; Windows SHALL require the same available identity checks before existing process-tree termination. It SHALL never infer ownership from a listening port. No runtime row, or an absent PID still associated with the selected runtime row, SHALL return idempotent success and clear only that stale row; reused, partial, inaccessible, or mismatched identity SHALL fail with actionable sanitized evidence and SHALL NOT signal any process.

#### Scenario: Stop cwd-owned runtime

- **WHEN** cwd resolves a running environment and the re-read runtime/environment records plus every required live-process identity check match
- **THEN** `odcli stop` terminates that exact owned process group through the existing process boundary, verifies exit, and clears its runtime row

#### Scenario: Stop explicitly selected runtime

- **WHEN** `odcli --env ENVIRONMENT stop` is invoked outside the worktree with matching live identity
- **THEN** it has the same plan, safety, output and exit behavior as cwd resolution

#### Scenario: Already stopped is idempotent

- **WHEN** the selected environment has no runtime row or its previously owned process is confirmed absent without PID reuse
- **THEN** `stop` succeeds without signaling a process

#### Scenario: Port occupancy is not ownership

- **WHEN** an unrelated process listens on the environment's recorded port or a PID has been reused
- **THEN** `stop` does not signal it and reports an ownership validation failure when stale or conflicting identity remains

### Requirement: Jira-key environment creation CLI

`odcli env create JIRA_TICKET --base REF` and compatible `odcli env checkout JIRA_TICKET --base REF` SHALL accept one repository-independent uppercase Jira key, omit the public CLI `--name` option, allocate the deterministic resolved branch defined by `development-environment`, and generate the existing `<project>:<resolved-branch>` environment name. They SHALL always create the resolved new branch from the selected effective base and SHALL never attach to a matching old local or remote ticket branch. `--create-venv` SHALL remain explicit and false by default. Rich, JSON and TOON dry-run/execution output SHALL expose the same captured resolved branch and bounded source evidence without fetching or contacting Jira.

#### Scenario: Help exposes Jira input

- **WHEN** root or environment-group help is rendered
- **THEN** the positional metavariable is `JIRA_TICKET`, `--name` is absent, both command spellings describe generated naming, and `--create-venv` remains optional

#### Scenario: Existing ticket receives next iteration

- **WHEN** `env create PROJ-123 --base dev` resolves prior ticket iterations
- **THEN** all plan, Git argv, environment name, catalogue, database/provenance and final output fields use the single captured next branch created from `dev`

#### Scenario: SDK exact-branch compatibility

- **WHEN** a caller uses the public `EnvironmentResource.checkout` exact-branch API directly
- **THEN** its existing branch and optional name contracts remain unchanged by the CLI-only Jira adapter

### Requirement: Environment configuration drift diagnostics

`odcli doctor` SHALL project one read-only typed drift result for each environment component `python`, `dependencies`, `odoo_config`, `addons`, and `git_provenance`. Each component SHALL contain `status=in_sync|drifted|unknown` and a sanitized concrete reason derived from current normalized project/environment inputs, stored applied evidence, and current artifacts. Rich SHALL give the existing relevant remediation (`env sync` only for Python/dependencies; recreate or a future explicit operation for other components), while JSON and TOON SHALL encode the same component statuses/evidence. The same internal projection SHALL be reusable by a future `env show` without adding that command in this change.

Diagnosis SHALL NOT update applied evidence, repair artifacts, call sync, change lifecycle state, fetch Git, or mutate catalogue/worktree/config/dependency/database/process state. Formatting/comments that preserve normalized semantic values SHALL remain `in_sync`; changed project defaults for a database or allocated port SHALL NOT mark the recorded environment binding drifted; ordinary worktree code changes SHALL remain Git context.

#### Scenario: Applied inputs drift independently

- **WHEN** Python selection, dependency inputs, managed Odoo values, or add-on paths differ from their last successful applied evidence
- **THEN** doctor marks only the corresponding components `drifted` and reports their applicable reason/remediation

#### Scenario: Semantically equal config remains synchronized

- **WHEN** comments, whitespace or formatting change without changing normalized managed Odoo/add-on values
- **THEN** doctor reports those components `in_sync`

#### Scenario: Git work is context, not drift

- **WHEN** the resolved branch/base provenance still matches but the worktree is dirty or ahead/behind
- **THEN** `git_provenance` remains `in_sync` and the ordinary Git activity is reported separately

#### Scenario: Diagnosis is inert and format-equivalent

- **WHEN** doctor runs for current, drifted, and legacy-unknown environments in Rich, JSON and TOON
- **THEN** all formats represent the same statuses and reasons and no applied snapshot or runtime resource changes

