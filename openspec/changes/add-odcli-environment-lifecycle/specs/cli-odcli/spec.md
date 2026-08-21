## ADDED Requirements

### Requirement: Click entry point

SDK MUST добавлять один Click entry point:

```toml
[project.scripts]
odcli = "odoo_instance_sdk.cli:cli"
```

CLI — тонкий adapter над SDK, не оркестратор процессов.

MVP command surface. Help и synopsis MUST показывать только эти команды, пока post-MVP срезы не начаты:

```text
odcli [--project PATH] COMMAND
odcli [--project PATH] [--env SELECTOR] <instance-command>

odcli init [OPTIONS]
odcli env checkout BRANCH [OPTIONS]
odcli env sync [ENVIRONMENT] [OPTIONS]
odcli env list [OPTIONS]
odcli env remove [ENVIRONMENT] [OPTIONS]
odcli run
odcli shell [-- ODOO_ARGS...]
odcli doctor [OPTIONS]
```

#### Scenario: MVP help shows only MVP commands

- **WHEN** `odcli --help` runs
- **THEN** shows `init`, `env checkout|sync|list|remove`, `run`, `shell`, `doctor`; no post-MVP commands

### Requirement: CLI not a third runtime

`odcli` MUST NOT: стартовать/останавливать/регистрировать процессы сам, держать process table, писать generated config в обход `EnvironmentResource`, реализовывать Git/`uv`/lock API, угадывать environment по recency или по «единственному ready».

`odcli` MAY: резолвить project/environment по двум правилам, печатать human text или один JSON envelope, брать exclusive/shared lock через internal SDK path, вызывать `EnvironmentResource` и `OdooInstance`.

#### Scenario: No process table in CLI

- **WHEN** `odcli run` executes
- **THEN** CLI calls `from_environment()` + `run_foreground()`, does not register/manage process itself

### Requirement: Context-aware command resolution

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

Для instance commands (`run`, `shell`; post-MVP также `eval`, `exec`, `module`, `translations`, `deps verify`):

1. Explicit root `--env SELECTOR` — UUID либо однозначное имя; option допустим только для instance commands.
2. Exact registered worktree containing current directory.
3. Иначе — ошибка: либо `cd` в worktree, либо `--env`, со списком candidates если их несколько.

Никогда не выбирать единственный `ready` молча и никогда не выбирать по recency.

#### Scenario: Explicit --env

- **WHEN** `odcli --env <uuid> run`
- **THEN** environment resolved from explicit selector

#### Scenario: Ambiguous name

- **WHEN** `odcli --env "feat" run` matches 2 environments
- **THEN** error with candidate list

### Requirement: Command-specific context rules

- `env checkout BRANCH`, default `env list` и `doctor` требуют project context;
- `env list --all-projects` читает durable global registry из любой directory и не требует project; default list ограничен current project, а `--all` означает include removed;
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

Bounded leaf commands принимают один `--json` после command (`odcli env list --json`). Без него выводят human table/text. `run` и interactive `shell` raw-streaming и `--json` не принимают.

JSON stdout содержит ровно один versioned envelope и никакого progress/log text:

```json
{
  "schema_version": 1,
  "ok": true,
  "command": "env.list",
  "context": {"project_source": "cwd", "environment_source": null},
  "data": {},
  "warnings": []
}
```

Structured error содержит stable `error.code`, message и optional hint/details; progress/external logs идут stderr, secrets redacted.

#### Scenario: JSON envelope

- **WHEN** `odcli env list --json` executes
- **THEN** stdout содержит ровно one versioned envelope, no progress/log text

#### Scenario: Secrets redacted

- **WHEN** error occurs during checkout
- **THEN** error message redacts passwords, config body, environment variables

### Requirement: Exit codes

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

1. Разрешить ready environment и проверить worktree/config, recorded Python и Odoo entry point.
2. Сравнить dependency fingerprint и при необходимости выполнить тот же environment sync. Если sync fails (compile error and no valid lock) — deterministic error, не запускать Odoo.
3. Проверить порт через stdlib `socket.bind((http_interface, http_port))`, а при занятом port выполнить только observational HTTP health check для диагностики.
4. Если port занят — независимо от HTTP ответа вернуть deterministic `port-conflict`/ownership-unknown и не менять generated config, не обновлять `used`, не запускать второй process.
5. Обновить `last_used_at` и generic `use/succeeded` event после free-port preflight.
6. Построить instance через `client.instance.from_environment(environment)`.
7. Передать управление `instance.run_foreground()` и вернуть его exit code.
8. На Ctrl+C `run_foreground()` останавливает только process group, созданную этим foreground call, и CLI завершается кодом 130.

Environment resource не запускает, не останавливает и не регистрирует процессы.

#### Scenario: Dependency sync failure blocks run

- **WHEN** `odcli run` and dependency fingerprint changed, sync fails (compile error, no valid lock)
- **THEN** deterministic error, Odoo not started

#### Scenario: Sync failure with valid lock — run continues

- **WHEN** `odcli run` and dependency fingerprint changed, sync fails (compile error), BUT valid `requirements.lock` already exists
- **THEN** run continues with existing valid lock (failed compile не заменяет valid lock)

#### Scenario: Port conflict deterministic error

- **WHEN** `odcli run` и `socket.bind((http_interface, http_port))` fails (port занят)
- **THEN** deterministic `port-conflict`/ownership-unknown, no second process, no config change, no `used` update

#### Scenario: Free port starts Odoo

- **WHEN** `odcli run` и port свободен
- **THEN** `last_used_at` + `use/succeeded` event, `from_environment()` → `run_foreground()` → exit code

### Requirement: `odcli shell`

```bash
odcli shell
odcli --env <environment-id> shell -- --log-level=debug
```

Алгоритм:

1. Выполнить тот же selector/config/Python/dependency preflight, что и `run`, без HTTP port check.
2. Использовать БД, привязанную к environment: source DB для `shared`, target DB для `copy`.
3. Построить обычный `OdooInstance` через `from_environment()`.
4. Вызвать `OdooInstance.shell()` с `[recorded-python, odoo-bin]`, одним config/DB. Passthrough config/database overrides запрещены.
5. Наследовать stdin/stdout/stderr, signals и exit code штатного `odoo-bin shell`.

#### Scenario: Shell from worktree

- **WHEN** `odcli shell` inside registered worktree
- **THEN** environment + DB inferred, `instance.shell()` executes with bound config/DB

#### Scenario: Shell sync failure with valid lock — continues

- **WHEN** `odcli shell` and dependency fingerprint changed, sync fails (compile error), BUT valid `requirements.lock` exists
- **THEN** shell continues with existing valid lock (same behavior as `run`)

### Requirement: `odcli doctor`

```bash
odcli doctor
odcli doctor --json
odcli --project /path/to/repo doctor
```

Read-only checks покрывают manifest, worktrees, `uv`, recorded Python/ownership, dependencies, Odoo/config, catalog, DB/backups, ports и orphaned artifacts.

`doctor` — CLI coordinator над `list`/`get`/`history` и filesystem checks. Это не `client.doctor` и не public resource.

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

### Requirement: Post-MVP command surface

Post-MVP, те же Click group, без новых public resources:

```text
odcli eval EXPRESSION [OPTIONS]
odcli exec SCRIPT [-- SCRIPT_ARGS...]
odcli module list [MODULE...] [OPTIONS]
odcli module update MODULE... [OPTIONS]
odcli module test MODULE... [OPTIONS]
odcli translations export --module MODULE... [OPTIONS]
odcli deps verify [OPTIONS]
odcli vscode generate [OPTIONS]
```

Эти команды не входят в первый CLI и не становятся public resources. Они используют captured local Odoo/uv primitives, no RPC fallback.

#### Scenario: Post-MVP not in MVP help

- **WHEN** `odcli --help` in MVP
- **THEN** `eval`/`exec`/`module`/`translations`/`deps`/`vscode` не показаны