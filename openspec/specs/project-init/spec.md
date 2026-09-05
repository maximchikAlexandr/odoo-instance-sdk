## Purpose

Secret-free project manifest creation, including headless/wizard init and VS Code launch import.
## Requirements
### Requirement: Project manifest location

`odcli init` MUST создавать один declarative manifest по пути `<repository-root>/.odcli/project.toml`.

Manifest содержит discovery/defaults: Odoo source/bin, source config, project Python interpreter (либо uv selector для explicit creation), dependency files, safe run args и `runtime_cwd`.

Secrets и runtime artifacts MUST NOT записываться в repository. Runtime artifacts остаются в platformdirs user directories и связываются с project через canonical Git common dir, а не через имя папки.

#### Scenario: Manifest создаётся в repository root

- **WHEN** `odcli init` выполняется в repository root
- **THEN** создаётся `.odcli/project.toml` с discovery/defaults, без secrets

#### Scenario: Runtime artifacts в user directories

- **WHEN** project manifest создан
- **THEN** runtime artifacts (worktrees, venvs, configs) живут в platformdirs user directories, связаны с project через canonical Git common dir

### Requirement: `ProjectConfig` public type

`ProjectConfig` MUST оставаться `msgspec.Struct` с `frozen=True` и предоставлять `ProjectConfig.load(project_path: str | Path) -> ProjectConfig`, читающую `.odcli/project.toml`.

Минимальные поля (существующие) plus новое optional `postgres: PostgresProjectConfig | None = None`.

`PostgresProjectConfig` MUST быть `msgspec.Struct` с `frozen=True, kw_only=True` со следующими полями:

- `mode: Literal["external", "compose"] = "external"`
- `image: str | None = None` — compose only
- `port: int | None = None` — compose only; `None` = allocate free loopback port at `init`
- `user: str | None = None` — compose only; default = source `db_user` or `"odoo"`

`ProjectConfig.to_manifest()` MUST писать `[postgres]` section только если поля не-default (т.е. для `mode="external"` без других полей — section опущена для backward compat).

Manifest MUST NOT содержать пароль или любые secret-like keys (через существующий `assert_no_secrets`).

#### Scenario: Manifest with postgres compose section

- **WHEN** `ProjectConfig` with `postgres=PostgresProjectConfig(mode="compose", image="pgvector/pgvector:pg16", port=5468, user="odoo")` is rendered
- **THEN** `to_manifest()` outputs `[postgres]` with `mode`, `image`, `port`, `user` but no `password`

#### Scenario: Legacy manifest without postgres section

- **WHEN** `ProjectConfig.load("/repo")` on a manifest without `[postgres]`
- **THEN** `config.postgres is None` (treated as external via source config downstream)

#### Scenario: Round-trip preserves postgres section

- **WHEN** `ProjectConfig` with `postgres=PostgresProjectConfig(mode="compose", ...)` is written and reloaded
- **THEN** reloaded `config.postgres` equals the original

#### Scenario: External mode omits postgres section by default

- **WHEN** `ProjectConfig` with `postgres=PostgresProjectConfig(mode="external")` (defaults) is rendered
- **THEN** `to_manifest()` omits `[postgres]` section (backward compat)

#### Scenario: Load existing manifest

- **WHEN** `ProjectConfig.load("/path/to/repo")` вызывается с существующим `.odcli/project.toml`
- **THEN** возвращается `ProjectConfig` с полями из manifest (включая optional `postgres`)

#### Scenario: Missing manifest

- **WHEN** `ProjectConfig.load("/path/to/repo")` вызывается без `.odcli/project.toml`
- **THEN** поднимается typed error с подсказкой `odcli init`

### Requirement: Interactive init wizard

`odcli init` в TTY MUST prompts только для unresolved required values. Fully specified values MUST NOT trigger prompts.

#### Scenario: Missing values prompt

- **WHEN** `odcli init` выполняется в TTY без `--odoo-bin`
- **THEN** Click prompt запрашивает Odoo bin path

#### Scenario: All specified — no prompts

- **WHEN** `odcli init --odoo-bin ... --python ... --config ...` выполняется в TTY со всеми required values
- **THEN** prompts не появляются

### Requirement: Headless init

`odcli init --no-input` MUST forbid prompts. Если required values missing/ambiguous, команда MUST fail с stable list of missing/ambiguous options.

#### Scenario: Missing required in no-input

- **WHEN** `odcli init --no-input` выполняется без `--odoo-bin`
- **THEN** команда fail с stable error listing missing `--odoo-bin`

#### Scenario: Fully specified no-input

- **WHEN** `odcli init --no-input --odoo-bin ... --python ... --config ...` выполняется
- **THEN** manifest создаётся без prompts

### Requirement: Idempotent init

An existing non-identical manifest MUST NEVER быть перезаписан silently. Identical init MUST быть no-op.

Для non-identical existing manifest:

- в TTY (interactive) — Click prompt подтверждение overwrite (yes/no);
- в `--no-input` — error с stable message "manifest exists and differs; remove it first or adjust options".

Флага forced overwrite (`--force`) нет в MVP.

#### Scenario: Identical re-init

- **WHEN** `odcli init` выполняется с опциями, идентичными существующему manifest
- **THEN** no-op, manifest не изменяется

#### Scenario: Non-identical existing manifest — TTY prompt

- **WHEN** `odcli init` в TTY с опциями, отличающимися от существующего manifest
- **THEN** Click prompt подтверждение overwrite; no silent overwrite

#### Scenario: Non-identical existing manifest — no-input error

- **WHEN** `odcli init --no-input` с опциями, отличающимися от существующего manifest
- **THEN** error "manifest exists and differs; remove it first or adjust options"; no prompt, no overwrite

### Requirement: Dry-run init

`odcli init --dry-run --json` MUST возвращать resolved manifest plus provenance (`option`, `vscode`, `discovery`, `default`) без записи на диск.

#### Scenario: Dry-run shows resolved manifest

- **WHEN** `odcli init --dry-run --json --odoo-bin ...` выполняется
- **THEN** JSON содержит resolved manifest с provenance, файлы не создаются

### Requirement: VS Code launch import

`odcli init --from-vscode <path> [--launch-name NAME]` MUST:

1. Parse VS Code JSON with comments and trailing commas (через `json5`).
2. Consider only `request=launch` Python/debugpy configurations with an Odoo-like `program`; MUST NOT select an unrelated first configuration.
3. In interactive mode show matching profiles; in `--no-input` require `--launch-name` when more than one candidate remains.
4. Import `python`, `program`, `cwd` and structured Odoo `args`: config, database, port, dev/addons/upgrade paths.
5. Support only static `${workspaceFolder}`; named-workspace, env, command, input and unresolved variables — explicit errors.
6. Report and drop operational `-u/-i/--stop-after-init`; never persist them as run defaults.
7. Record source file/profile as non-secret provenance, then use generated project manifest at runtime; later edits to `launch.json` do not silently change an environment.

`preLaunchTask` and `.vscode/tasks.json` MUST be reported as ignored. Import MUST NEVER execute editor tasks or arbitrary shell commands.

`envFile` и inline `env` для MVP лишь report'ятся как ignored; values не читаются, не копируются и не печатаются.

#### Scenario: Fixture selects Odoo profile, not first Node

- **WHEN** `odcli init --from-vscode /odoo/comerta/.vscode/launch.json` выполняется с fixture содержащим Node и `Odoo comerta` profiles
- **THEN** importer выбирает `Odoo comerta`, не первый Node profile

#### Scenario: Imports external paths and dev mode

- **WHEN** fixture `/odoo/comerta/.vscode/launch.json` импортируется
- **THEN** imported external Python/Odoo/config paths, `CMRT-361_1`, port seed `8068`, `--dev=qweb,xml`

#### Scenario: Drops operational args

- **WHEN** launch profile содержит `-u comerta_base`
- **THEN** importer reports and drops `-u comerta_base`, не persist'ит как run default

#### Scenario: Unresolved variable error

- **WHEN** launch profile содержит `${env:PYTHON_PATH}`
- **THEN** import завершается explicit error

#### Scenario: Multiple candidates require launch-name

- **WHEN** `odcli init --no-input --from-vscode launch.json` finds >1 Odoo-like profile без `--launch-name`
- **THEN** команда fail с list of candidate profile names

### Requirement: VS Code launch mapping

VS Code launch mapping MUST use the following destinations:

| VS Code field/arg | Project/environment destination |
|---|---|
| `python` | `ProjectConfig.python`; default checkout reuses this existing venv interpreter |
| `program` | `ProjectConfig.odoo_bin` |
| `-c`/`--config` | `source_config` |
| `-d`/`--database` | `default_source_database` |
| `--http-port` | `preferred_http_port` seed; checkout повторно проверяет free/unique |
| `--addons-path`/`--upgrade-path` | config overlays; repo-local entries rebase в worktree, external remain absolute with warning |
| `--dev` | safe `default_run_args`; shell/automation не наследуют его неявно |
| `cwd` | `runtime_cwd` по правилам ниже |

`cwd` становится `ProjectConfig.runtime_cwd`:

- repo-local path хранится относительно manifest и при checkout rebases в worktree;
- external absolute path остаётся неизменным с portability warning.

При checkout resolved `runtime_cwd` snapshot'ится в environment; runtime не зависит от дальнейших изменений manifest/launch file.

#### Scenario: Repo-local cwd rebased at checkout

- **WHEN** `runtime_cwd` — repo-local relative path, checkout создаёт worktree
- **THEN** resolved runtime_cwd в environment rebased на worktree path

#### Scenario: External absolute cwd preserved

- **WHEN** `runtime_cwd` — external absolute path `/opt/odoo`
- **THEN** resolved runtime_cwd остаётся `/opt/odoo` с portability warning

### Requirement: `odcli init` postgres options

`odcli init` MUST принимать опции:

```text
--postgres [external|compose]   default: external
--postgres-image IMAGE          compose only; required with --no-input
--postgres-port PORT            compose only; omitted = allocate free loopback port
--postgres-user USER            compose only; default: source db_user or "odoo"
```

Interactive init MUST prompts только для unresolved mode-specific values. `--no-input` MUST forbid prompts и MUST require `--postgres-image` для `compose` mode.

`--dry-run --json` MUST сообщать resolved non-secret plan including `postgres` section (mode, image, port, user, allocated_port flag) без записи файлов.

`init` MUST NOT запускать `docker compose up`. `init` MUST NOT создавать compose artifacts directory (создаётся lazily при первом `up`).

Для `compose` без `--postgres-port` — аллоцировать free loopback port через `probe_address` и persist в manifest. Для `external` без `--postgres-*` — `[postgres]` section опущена (backward compat).

Idempotency comparison MUST учитывать `[postgres]` section.

#### Scenario: Compose no-input requires image

- **WHEN** `odcli init --no-input --postgres compose` without `--postgres-image`
- **THEN** command fails with stable error listing missing `--postgres-image`

#### Scenario: Compose allocates free port

- **WHEN** `odcli init --postgres compose --postgres-image pgvector/pgvector:pg16` without `--postgres-port`
- **THEN** a free loopback port is allocated and persisted in manifest

#### Scenario: Compose with explicit port

- **WHEN** `odcli init --postgres compose --postgres-image pgvector/pgvector:pg16 --postgres-port 5468`
- **THEN** manifest contains `port = 5468`

#### Scenario: Compose user defaults to source db_user

- **WHEN** `odcli init --postgres compose --postgres-image ... --config odoo.conf` where `odoo.conf` has `db_user=alice`
- **THEN** manifest `user = "alice"`

#### Scenario: Compose user defaults to odoo without source config

- **WHEN** `odcli init --postgres compose --postgres-image ...` without `--postgres-user` and no source config
- **THEN** manifest `user = "odoo"`

#### Scenario: External default omits postgres section

- **WHEN** `odcli init --odoo-bin ... --config ...` without `--postgres` flags
- **THEN** manifest has no `[postgres]` section (default external)

#### Scenario: Dry-run reports postgres plan

- **WHEN** `odcli init --dry-run --json --postgres compose --postgres-image ...` is run
- **THEN** JSON envelope contains `postgres` with `mode`, `image`, `port`, `user`, `allocated_port` and no secrets; files not written

#### Scenario: Init does not start Docker

- **WHEN** `odcli init --postgres compose --postgres-image ...` completes successfully
- **THEN** no `docker compose up` is invoked, no artifacts directory is created

#### Scenario: Idempotent re-init with same postgres section

- **WHEN** `odcli init` runs with options identical to existing manifest (including `[postgres]`)
- **THEN** no-op, manifest unchanged

### Requirement: Project database preparation configuration

`ProjectConfig` SHALL add:

- `default_base_ref: str | None = None` under `[project]`;
- `refresh_after_hours: float | None = None` under `[project]`, finite and strictly greater than zero;
- `test_instance: TestInstanceProjectConfig | None = None` from a top-level `[test_instance]` table.

`TestInstanceProjectConfig` SHALL be a frozen, keyword-only `msgspec.Struct` with required non-empty `base_url` and `database`, plus optional non-empty `git_branch`. URL normalization SHALL use the existing base-URL rules. The table SHALL never accept a master-password/password/secret field. Unknown keys SHALL fail closed.

#### Scenario: Preparation config round-trip

- **WHEN** a manifest contains `default_base_ref`, `refresh_after_hours`, and `[test_instance]` URL/database/branch
- **THEN** `ProjectConfig.load()` validates them and `to_manifest()` round-trips the same non-secret values

#### Scenario: Legacy project remains valid

- **WHEN** a legacy manifest omits all preparation settings
- **THEN** it loads with `None` defaults and existing project behavior remains available

#### Scenario: Secret-like test key rejected

- **WHEN** `[test_instance]` contains `master_password` or another unknown key
- **THEN** loading/writing fails and the value is not echoed

### Requirement: Effective checkout base precedence

The project manifest's `default_base_ref` SHALL be the checkout default only when the current checkout call does not supply an explicit base. Explicit `EnvironmentCheckoutOptions.base_ref` / CLI `--base` SHALL take precedence. If both are absent, the existing `HEAD` fallback SHALL remain.

#### Scenario: Explicit base wins

- **WHEN** project default is `develop` and checkout supplies `--base release/19`
- **THEN** effective base is `release/19`

#### Scenario: Manifest default applies

- **WHEN** checkout supplies no base and project default is `develop`
- **THEN** effective base is `develop`

### Requirement: Atomic default database updates preserve manifest intent

Database preparation SHALL update only `default_source_database` through the existing atomic, secret-checked manifest writer. It SHALL preserve preparation, PostgreSQL, runtime, and all unrelated project fields. The write SHALL occur under the project preparation lock after reloading the current file and detecting conflicting relevant edits.

#### Scenario: Refresh switches one field

- **WHEN** a restored preparation completes successfully
- **THEN** the manifest is atomically replaced with only the default database changed and all other settings preserved

### Requirement: Project-local environment loading

After resolving an initialized project and before constructing project runtime operations, `odcli` SHALL look only for `<project-root>/.odcli/.env`; it SHALL NOT walk above the resolved project or load cwd-global dotenv files. A missing file SHALL be ignored. An unreadable or malformed file SHALL fail with a path-and-line-number-only sanitized error before mutation.

The UTF-8 grammar SHALL be deterministic: after stripping a UTF-8 BOM on the first line, a physical line is blank, a comment whose first non-whitespace character is `#`, or an assignment `[ \t]*KEY[ \t]*=[ \t]*VALUE[ \t]*`; `KEY` SHALL match `[A-Za-z_][A-Za-z0-9_]*`. Unquoted `VALUE` SHALL preserve internal whitespace, trim surrounding horizontal whitespace, and treat `#` as data. Single-quoted values SHALL contain any character except single quote, backslash, CR, LF, or NUL and SHALL perform no escaping. Double-quoted values SHALL support only `\\`, `\"`, `\n`, `\r`, and `\t` escapes. Empty values and `KEY=` SHALL be valid. Multiline values, `export`, duplicate keys, trailing tokens after a quoted value, unknown escapes, interpolation (`$NAME`/`${NAME}`), command substitution, backticks, NULs, and invalid UTF-8 SHALL be rejected; no shell evaluation SHALL occur.

The loader SHALL create an immutable effective mapping in which the invoking process value wins over the file value for every key. File-derived ordinary variables SHALL be propagated only to Odoo runtime children (foreground run, Odoo shell/eval/exec, module operations, tests, and Odoo-backed restore steps). They SHALL NOT be propagated to Git, PostgreSQL/psql/pg tools, Docker/Compose, editors, browsers, package/build tools, or any other child class; those children SHALL retain their existing purpose-built sanitized environments. `ODCLI_TEST_MASTER_PASSWORD` SHALL be classified as secret, consumed only by restore coordination, removed before every child spawn including Odoo, and never exported globally. Keys matching the existing credential/secret classifier SHALL be redacted from all public surfaces; classification SHALL not by itself authorize propagation to a denied child class. Loaded values SHALL never mutate `os.environ`.

#### Scenario: Process environment wins
- **WHEN** `.odcli/.env` and the invoking process both define `ODCLI_TEST_MASTER_PASSWORD`
- **THEN** the existing process value is used and neither value is printed

#### Scenario: Search stops at project boundary
- **WHEN** an initialized project has no `.odcli/.env` but a parent directory does
- **THEN** the parent file is not loaded

#### Scenario: Malformed file fails before work
- **WHEN** the resolved project file contains an invalid assignment
- **THEN** the command fails before child creation or mutation without echoing the line's value

#### Scenario: Grammar and escaping are exact
- **WHEN** the file contains blank/comments, valid identifiers, empty/unquoted/single-quoted/double-quoted values and supported double-quote escapes
- **THEN** values decode exactly once according to the grammar, while duplicate keys, interpolation, unsupported escapes, multiline values, or trailing quoted tokens fail before work

#### Scenario: Odoo child receives ordinary values
- **WHEN** a file-defined ordinary variable is not overridden by the process and an Odoo runtime child is spawned
- **THEN** that child receives the value while Git, PostgreSQL, Docker/Compose, and other child classes do not

#### Scenario: Master password is consumed, not propagated
- **WHEN** restore coordination resolves `ODCLI_TEST_MASTER_PASSWORD` from the effective mapping
- **THEN** it uses the value for the privileged restore decision and no spawned child or public projection receives the key or value

### Requirement: Project-local secret-file hygiene

Project initialization SHALL ensure `.odcli/.env` is covered by the repository ignore rules and documentation SHALL require owner-only readability. Loading an existing file with group/other permission bits SHALL fail closed with a path-only remediation message. Secret keys and values SHALL be excluded from Rich, JSON, TOON, dry-run plans, errors, diagnostics, logs, and fingerprints.

#### Scenario: Insecure permissions are refused
- **WHEN** `.odcli/.env` is readable or writable by group or others on a platform supporting POSIX mode bits
- **THEN** loading fails before use and advises owner-only permissions without revealing contents
