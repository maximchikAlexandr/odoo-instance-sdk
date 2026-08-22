## ADDED Requirements

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

`ProjectConfig` MUST быть `msgspec.Struct` с `frozen=True` и предоставлять `ProjectConfig.load(project_path: str | Path) -> ProjectConfig`, читающую `.odcli/project.toml`.

Минимальные поля:

- `odoo_bin: Path | None`
- `python: str | Path | None`
- `source_config: Path | None`
- `default_source_database: str | None`
- `preferred_http_port: int | None`
- `requirements: tuple[str, ...]`
- `default_run_args: tuple[str, ...]`
- `runtime_cwd: Path | None`

`ProjectConfig` MUST быть immutable declarative discovery/defaults, не runtime state.

#### Scenario: Load existing manifest

- **WHEN** `ProjectConfig.load("/path/to/repo")` вызывается с существующим `.odcli/project.toml`
- **THEN** возвращается `ProjectConfig` с полями из manifest

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