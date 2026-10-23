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

`ProjectConfig` SHALL make `test_instance.database` optional. The parser and writer SHALL round-trip a config without `database` without adding it. When it is explicitly set, it SHALL be used without requiring database list availability. When it is absent, remote-refresh preflight SHALL obtain names through the existing `DatabaseResource.names()` and select exactly one; zero SHALL fail with `remote_database_none`; many SHALL fail with `remote_database_ambiguous` listing names; an unavailable list SHALL fail with `remote_database_list_unavailable`. All three fail before download. COPY checkout SHALL refuse an unresolved named source before mutation and direct the caller to `db refresh --remote` or an explicit `--backup`. The auto-detected name SHALL NOT be written back to `project.toml`.

#### Scenario: optional database round-trips

- **WHEN** a `ProjectConfig` without `test_instance.database` is parsed and written back
- **THEN** the written manifest does not add `database`

#### Scenario: explicit database has priority

- **WHEN** `test_instance.database` is explicitly set
- **THEN** it is used and database list availability is not required

#### Scenario: absent database is resolved for refresh

- **WHEN** `test_instance.database` is absent and the instance exposes exactly one database
- **THEN** that database is selected for the current operation and the name is not written to `project.toml`

#### Scenario: COPY refusal is fail-closed

- **WHEN** COPY checkout selects a named source without a database and without an explicit backup
- **THEN** it fails before download, local database mutation, or manifest mutation with the supported refresh/backup alternative

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

#### Scenario: absent database is resolved from the instance

- **WHEN** `test_instance.database` is absent and the instance exposes exactly one database
- **THEN** that database is selected for the current operation and the name is not written back to `project.toml`

#### Scenario: unavailable list is a distinct error

- **WHEN** `test_instance.database` is absent and the database list is unavailable
- **THEN** the operation fails before download with `remote_database_list_unavailable`

### Requirement: Interactive init wizard

`odcli init` в TTY MUST prompts только для unresolved required values и необходимых подтверждений. Fully specified values MUST NOT trigger value prompts. Одобрение Compose-образа запрашивается отдельно, если digest ещё не одобрен и отсутствует `--yes`.

#### Scenario: Missing values prompt

- **WHEN** `odcli init` выполняется в TTY без `--odoo-bin`
- **THEN** Click prompt запрашивает Odoo bin path

#### Scenario: All specified — no prompts

- **WHEN** `odcli init --odoo-bin ... --python ... --config ...` выполняется в TTY со всеми required values
- **THEN** prompts не появляются

### Requirement: Headless init

`odcli init --no-input` SHALL accept `--test-url`, `--test-database`, `--test-branch`, `--local-config`, and `--allow-partial`. When the setup is incomplete and `--allow-partial` is absent, it SHALL fail with `init_incomplete` before writes and change no manifest, config, dotenv, catalog, or cluster resources. `--yes` SHALL NOT bypass the completeness check. When `--allow-partial` is explicit, partial initialization SHALL proceed and the result SHALL contain a `partial initialization` warning with the missing capabilities. A fully-specified setup SHALL NOT ask any question and SHALL remain automatable. `--dry-run` SHALL NOT prompt.

#### Scenario: Missing required in no-input

- **WHEN** `odcli init --no-input` выполняется без `--odoo-bin`
- **THEN** команда fail с stable error listing missing `--odoo-bin`

#### Scenario: Fully specified no-input

- **WHEN** `odcli init --no-input --odoo-bin ... --python ... --config ...` выполняется
- **THEN** manifest создаётся без prompts

#### Scenario: no-input incomplete fails without allow-partial

- **WHEN** `odcli init --no-input --yes` runs with an incomplete setup and without `--allow-partial`
- **THEN** it fails with `init_incomplete` before writes and changes nothing

#### Scenario: no-input allow-partial proceeds

- **WHEN** `odcli init --no-input --allow-partial` runs with an incomplete setup
- **THEN** partial initialization proceeds and the result warns about the missing capabilities

#### Scenario: full setup is automatable

- **WHEN** `odcli init --no-input --yes` runs with a fully-specified setup
- **THEN** no question is asked and the project is fully initialized

### Requirement: Image consent within Compose init

Compose `init --yes` SHALL resolve the selected image to an exact OCI RepoDigest and persist approval through the existing per-project user-data trust store before writing project configuration or preparing the venv. Interactive Rich init without `--yes` SHALL ask for confirmation displaying the selected reference and resolved digest; a matching existing approval SHALL skip the question. Refusal or failed image resolution SHALL leave project files, venv, PostgreSQL password and existing approval unchanged. This phase SHALL use the same immutable init command plan and process/action ledger as preparation, cluster startup and `tmp` bootstrap, without a nested CLI invocation. Init SHALL continue in the same invocation after approval; callers SHALL NOT need a separate `approve-image` and retry. Noninteractive init without `--yes` SHALL NOT create approval implicitly. External PostgreSQL SHALL NOT resolve or approve an image. Existing lifecycle checks SHALL still reject digest changes after approval.

Compose `init --yes --dry-run` SHALL expose image resolution and approval steps without pulling, inspecting, prompting, or writing approval. `--yes` SHALL NOT bypass completeness, configuration or other safety validations.

#### Scenario: One confirmed initialization

- **WHEN** `init --postgres compose --postgres-image IMAGE --yes` executes with valid inputs and an unapproved image
- **THEN** it resolves and approves the exact digest, then prepares and bootstraps the project in one invocation

#### Scenario: Declined or unavailable image

- **WHEN** the user declines image confirmation or image pull/inspection fails
- **THEN** init stops before project/venv/password mutation and preserves any existing approval

#### Scenario: Matching or changed approval

- **WHEN** the resolved digest matches the saved approval
- **THEN** init continues without asking for image confirmation
- **WHEN** it differs and the caller supplied `--yes` or confirms interactively
- **THEN** the newly resolved digest replaces the approval; startup still rechecks it

#### Scenario: Preview has no image effects

- **WHEN** Compose init runs with `--yes --dry-run`
- **THEN** the plan includes image resolution and approval but executes neither processes nor approval writes

### Requirement: Idempotent init

An existing non-identical manifest MUST NEVER be overwritten silently. Identical init MUST preserve the manifest and MUST report a no-op only after every required lifecycle postcondition for the selected project mode is verified. For Compose init, an identical retry SHALL execute the existing immutable ensure-running and bootstrap verification flow when the owned cluster or SQL-proven `tmp` bootstrap is incomplete, and SHALL report success only after those postconditions hold. A fully complete identical Compose init SHALL NOT recreate `tmp`. `--yes` SHALL be the only non-interactive overwrite confirmation and SHALL work with `--no-input`; without it, non-identical headless or machine init SHALL retain the stable error `manifest exists and differs; remove it first or adjust options`. Interactive Rich init without `--yes` SHALL retain the Click confirmation prompt. `--yes` SHALL NOT weaken validation or make `--dry-run` mutating. Every real no-op response SHALL report the requested `dry_run` value truthfully.

#### Scenario: Identical complete re-init

- **WHEN** `odcli init` is run with options identical to an existing manifest and all required lifecycle postconditions are complete
- **THEN** it is a no-op, the manifest is unchanged, `tmp` is not recreated, and a non-dry-run machine response reports `dry_run=false`

#### Scenario: Identical re-init

- **WHEN** `odcli init` is run with options identical to the existing manifest
- **THEN** it is a no-op and the manifest is unchanged

#### Scenario: Identical incomplete Compose re-init

- **WHEN** a prior Compose init wrote the manifest and generated config but failed before the owned cluster and SQL-proven `tmp` bootstrap completed
- **THEN** an identical retry executes the captured Compose/bootstrap flow and cannot report success until both postconditions hold

#### Scenario: Incomplete retry still fails

- **WHEN** an identical Compose retry cannot satisfy image trust, cluster identity, startup, or SQL bootstrap verification
- **THEN** init exits unsuccessfully with the precise existing boundary error and does not publish a successful no-op result

#### Scenario: Interactive overwrite prompt

- **WHEN** interactive Rich `odcli init` resolves a manifest different from the existing manifest and `--yes` is absent
- **THEN** it asks for Click overwrite confirmation and performs no silent overwrite

#### Scenario: Non-identical existing manifest — TTY prompt

- **WHEN** `odcli init` runs in a TTY with options different from the existing manifest and `--yes` is absent
- **THEN** Click requests overwrite confirmation and no silent overwrite occurs

#### Scenario: Non-identical existing manifest — headless error

- **WHEN** `odcli init --no-input` resolves a different manifest without `--yes`
- **THEN** it emits the stable refusal, does not prompt, and does not overwrite

#### Scenario: Non-identical existing manifest — no-input error

- **WHEN** `odcli init --no-input` resolves options different from the existing manifest without `--yes`
- **THEN** it emits `manifest exists and differs; remove it first or adjust options`, does not prompt, and does not overwrite

#### Scenario: Headless overwrite remains refused by default

- **WHEN** `odcli init --no-input` resolves a different manifest without `--yes`
- **THEN** it emits the stable refusal, does not prompt, and does not overwrite

#### Scenario: Explicit headless overwrite

- **WHEN** `odcli init --no-input --yes` resolves a valid manifest different from the existing manifest
- **THEN** it atomically replaces the generated init artifacts without a prompt

#### Scenario: Confirmed dry-run remains inert

- **WHEN** `odcli init --no-input --yes --dry-run` resolves a different manifest
- **THEN** it reports the same resolved plan without changing any file or catalogue record

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

Project initialization SHALL ensure `.odcli/.env` is ignored by an ignore file stored under `.odcli`; it SHALL NOT create, append to, replace, or otherwise modify the repository-root `.gitignore`. Documentation SHALL require owner-only readability. Loading an existing file with group/other permission bits SHALL fail closed with a path-only remediation message. Secret keys and values SHALL be excluded from Rich, JSON, TOON, dry-run plans, errors, diagnostics, logs, and fingerprints.

#### Scenario: Init leaves root ignore unchanged

- **WHEN** init creates or updates local SDK artifacts in a repository with or without a root `.gitignore`
- **THEN** `.odcli/.env` is covered by `.odcli`-local ignore rules and the root `.gitignore` bytes and metadata are unchanged

#### Scenario: Insecure permissions are refused

- **WHEN** `.odcli/.env` is readable or writable by group or others on a platform supporting POSIX mode bits
- **THEN** loading fails before use and advises owner-only permissions without revealing contents

### Requirement: Compose init produces a usable project runtime config

A full self-contained `init` with `--local-config` and Compose PostgreSQL SHALL produce: an owned Compose cluster, a generated `.odcli/odoo.conf` as the effective local `source_config`, `data_dir` set to the absolute `{project_root}/.odcli/filestore`, ordered absolute `addons_path` values resolved from stored addon roots, stored `server_wide_modules`, a `.odcli/.env` under `0600` with `ODCLI_TEST_INSTANCE_ORIGIN_PINS` and an empty `ODCLI_TEST_MASTER_PASSWORD=`, a valid `[test_instance]` when `--test-url`/`--test-branch` are provided (and `--test-database` or a uniquely listable remote database), and a valid Odoo bootstrap database `tmp` with `base` installed. The existing generator SHALL accept an absent source config, preserve applicable source options when one exists, and overlay project-owned runtime values. Master password SHALL NOT be accepted as a CLI argument and SHALL NOT be stored in manifest/history. Re-running `init` without test-instance, addon-root, or server-wide-module options SHALL preserve existing valid values. Completeness groups follow design D5. Bootstrap `tmp` follows design D5a.

#### Scenario: Local Database Manager password is initialized and preserved

- **WHEN** Compose init writes or repairs the project-owned generated config
- **THEN** a nonblank source `admin_passwd` is preserved; otherwise an existing nonblank generated-config value is reused
- **AND** if both are absent or blank, init generates an independent cryptographically random local master password during execution and writes `admin_passwd` atomically in the owner-only `0600` config
- **AND** re-init and generated-config repair preserve that password; imported source files remain unchanged
- **AND** dry-run generates no secret and changes no files; the password is absent from manifest, command snapshots, Rich/JSON/TOON output, errors, logs and fingerprints
- **AND** legacy generated configs missing a nonblank `admin_passwd` are detected as needing repair

#### Scenario: Imported Compose project is ready after init

- **WHEN** an existing project is initialized from a VS Code launch config and then confirmed with Compose PostgreSQL settings
- **THEN** project-level database and Odoo commands resolve the generated local config bound to the owned cluster without manual file editing

#### Scenario: Imported config is immutable

- **WHEN** Compose init generates its local runtime config from an external `source_config`
- **THEN** the source config is byte-for-byte unchanged and only the project-owned generated config contains the effective cluster binding

#### Scenario: Compose password remains secret

- **WHEN** the generated config is bound to the owned cluster password
- **THEN** the password is absent from the manifest and every Rich, JSON, TOON, error, log, dry-run, and fingerprint surface

#### Scenario: External PostgreSQL behavior is unchanged

- **WHEN** a project uses external PostgreSQL mode
- **THEN** init retains the existing source-config runtime behavior and does not create Compose artifacts

#### Scenario: full self-contained init

- **WHEN** `odcli init --from-vscode .vscode/launch.json --launch-name "..." --postgres compose --postgres-image postgres:12 --test-url http://example --test-database db --test-branch main --local-config --yes` runs
- **THEN** the project gets an owned Compose cluster, generated local config, `data_dir` `{project_root}/.odcli/filestore`, `.odcli/.env` under `0600`, a valid `[test_instance]`, and bootstrap database `tmp` with `base` installed

#### Scenario: re-run preserves test_instance

- **WHEN** `odcli init` runs again without test-instance options
- **THEN** the existing `[test_instance]` is preserved

#### Scenario: database is not a missing group when the list has one name

- **WHEN** execute-mode `init` has `test_url` and `test_branch` but no `--test-database`, and `DatabaseResource.names()` returns exactly one name
- **THEN** `test_database` is not listed as a missing group

#### Scenario: dry-run completeness does not call names

- **WHEN** `odcli init --dry-run` runs without `--test-database` and without `database` in the manifest
- **THEN** `test_database` is listed as missing and `DatabaseResource.names()` is not called

#### Scenario: Generated config contains ordered project inputs

- **WHEN** Compose init stores multiple addon roots and server-wide modules with or without a source config
- **THEN** generated `addons_path` contains repository-root-resolved absolute paths and `server_wide_modules` contains names in stored order before bootstrap starts

### Requirement: Generated Compose config is independently idempotent

Init SHALL validate `.odcli/odoo.conf` independently of an unchanged `project.toml`; missing or stale generated config SHALL be atomically regenerated from current manifest, source config, port, cluster binding, addon roots, and server-wide modules without rewriting manifest/source config. Current config SHALL remain a true no-op, mode `0600`, tracked/symlink/unsafe targets SHALL fail before partial writes, and dry-run SHALL expose the need without files or secrets. [Source: GH#64 §3]

#### Scenario: Repair missing generated config

- **WHEN** the manifest is unchanged but generated Compose config is missing or stale
- **THEN** re-init repairs only generated config and preserves source and manifest bytes

#### Scenario: Repair after stored config-input change

- **WHEN** stored addon roots or server-wide modules differ from the values rendered in generated Compose config
- **THEN** re-init repairs only generated config with the current ordered values before any bootstrap action

### Requirement: Git commit settings
New project manifests SHALL explicitly write tracker-neutral `ticket_link_enabled` and `ticket_base_url` settings for Odoo commit generation; historical alpha projects with the removed vendor-specific enabled setting but no URL SHALL fail with migration guidance, project values SHALL override an already-existing global setting, and no global-config subsystem SHALL be introduced solely here. `ticket_base_url` SHALL allow a tracker path, normalize its trailing slash, and reject embedded credentials, query parameters, and fragments. Odoo instance base URLs SHALL retain their origin-only validation. Supported config identifiers SHALL NOT name a ticket vendor. [Sources: GH#65; GH#64 §11]

#### Scenario: Initialize Git commit policy
- **WHEN** a new project is initialized with defaults
- **THEN** its manifest contains explicit `ticket_link_enabled` and `ticket_base_url` values consumed by Git commit planning and no vendor-specific setting

#### Scenario: Configure a tracker below the site root
- **WHEN** a project manifest sets `ticket_base_url` to `https://tracker.example/issues/`
- **THEN** the loaded and serialized setting is `https://tracker.example/issues`
- **AND** credentials, query parameters, and fragments remain invalid

### Requirement: Self-contained Compose init creates bootstrap database `tmp`

Self-contained Compose `init` SHALL create Odoo database `tmp` with `base` installed through the same project runtime. Spawn argv SHALL come from `resolve_runtime_argv` / `_build_cli_args` plus `--database tmp --init=base --stop-after-init` as a ProcessStep via `internal/proc` with `shell=False`. A separate Odoo version or second launcher SHALL NOT be introduced. After `--stop-after-init` the process has exited: readiness SHALL be SQL `SELECT state FROM ir_module_module WHERE name = 'base'` on database `tmp` returning `installed` (owned cluster ActionStep). `POST /web/webclient/version_info` SHALL NOT run at `init`. The operation SHALL be idempotent: that SQL success SHALL NOT recreate; missing relation or state not `installed` SHALL fail with `init_bootstrap_failed`. `--dry-run` SHALL show the step and SHALL NOT spawn. A failure SHALL NOT return a successful project-setup result. If an owned Compose project has no valid `tmp`, the first `odcli run` SHALL run the same ProcessStep+SQL.

The foreground `run` command SHALL capture the bootstrap spawn, probe, readiness probe, and verification action in its immutable execution plan and SHALL execute those same captured steps before spawning the requested Odoo process.

#### Scenario: init creates a valid tmp with base

- **WHEN** a full self-contained `init` with Compose PostgreSQL completes
- **THEN** database `tmp` exists and SQL `SELECT state FROM ir_module_module WHERE name = 'base'` returns `installed`

#### Scenario: idempotent tmp

- **WHEN** `init` runs again and a valid `tmp` already exists
- **THEN** `tmp` is not recreated

#### Scenario: invalid same-named database is rejected

- **WHEN** `init` runs and a same-named `tmp` exists but is invalid
- **THEN** the command fails with `init_bootstrap_failed` and does not accept it as ready

#### Scenario: dry-run shows the tmp step

- **WHEN** `init --dry-run` runs with Compose PostgreSQL
- **THEN** the `tmp` initialization step is shown and no database is created

#### Scenario: first foreground run captures bootstrap

- **WHEN** a foreground `odcli run` command is constructed for an owned Compose project
- **THEN** its plan contains the bootstrap spawn, probe, readiness probe, and verification action that execution consumes

### Requirement: Named remote sources in project configuration

Project configuration SHALL support multiple named remote entries with normalized URL, optional database, and declared Git branch. Names SHALL match `[a-z][a-z0-9_]*`; invalid or duplicate names, unknown fields, and credential-bearing URLs SHALL fail validation. The public project-init surface SHALL expose listing, one immutable configure operation with explicit replace semantics, and immutable removal using the existing manifest; no additional client facade SHALL be introduced. Configuration commands SHALL capture canonical repository identity and manifest fingerprint, take the existing project lock, reject drift, preserve unrelated project values, and SHALL NOT contact remote servers or delete secrets or operational resources.

#### Scenario: Configure lab and staging

- **WHEN** a project adds lab and staging with different URLs, databases and branches
- **THEN** both entries round-trip independently and can be selected by exact name

#### Scenario: Configure a source without a database

- **WHEN** a project adds a named source without a database
- **THEN** the entry round-trips with `database` omitted and no remote request is made

#### Scenario: Idempotent and concurrent edits

- **WHEN** the same entry is added again
- **THEN** it is a no-op; a different existing entry requires explicit update
- **AND** a manifest changed since planning causes a conflict without overwriting another edit

#### Scenario: Remove a profile

- **WHEN** an existing named entry is removed
- **THEN** only that configuration entry is removed; backups, secrets and environments remain intact

### Requirement: Initialization accepts named sources

Init SHALL accept multiple typed remote entries in SDK and repeatable `--remote NAME URL GIT_REF` or `--remote NAME URL DATABASE GIT_REF` in CLI, applying existing validation, no-input, overwrite and dry-run rules. Named entries SHALL satisfy the remote-configuration completeness check without requiring a legacy test entry. An omitted database SHALL remain omitted and be resolved only by a later refresh operation. Re-init without remote inputs SHALL preserve existing entries. Credential availability SHALL be reported only as derived `ODCLI_REMOTE_<UPPER_NAME>_MASTER_PASSWORD` key names; secrets SHALL NOT be accepted by init, enter the manifest, or be written to `.odcli/.env`.

#### Scenario: Headless initialization with an optional database

- **WHEN** complete local init options and `--remote staging https://staging.example dev` are supplied with no-input
- **THEN** init writes the named source without a database, without prompting for a legacy source or a password

#### Scenario: Four-value initialization preserves explicit database

- **WHEN** init receives `--remote staging https://staging.example staging_db dev`
- **THEN** the source stores `staging_db` and the declared branch

#### Scenario: Re-init preserves existing sources

- **WHEN** init is repeated without remote options
- **THEN** it retains every named and legacy remote entry, including omitted databases

#### Scenario: Headless initialization with two sources

- **WHEN** complete local init options and two valid named sources are supplied with no-input
- **THEN** init writes both entries without prompting for a legacy test source or a password
- **AND** reports which named-source passwords still need configuration, without requesting origin approval variables

#### Scenario: Preserve existing sources

- **WHEN** init is repeated without remote options
- **THEN** it retains every named and legacy remote entry

#### Scenario: Dry-run or invalid inputs

- **WHEN** init previews remote additions or receives conflicting duplicate names
- **THEN** dry-run writes nothing and conflicting inputs fail before any init mutation

### Requirement: Compose bootstrap appends exact lifecycle evidence

After a self-contained Compose initialization or first project run actually creates database `tmp` and the existing SQL readiness check proves `base` is installed, the system SHALL append one `bootstrapped` event to the existing `database_events` lifecycle for the normalized cluster endpoint and exact database `tmp`. The event SHALL contain the exact current active `cluster_id` and project-owned data directory and SHALL keep restore-only fields null. The catalog write SHALL be an explicit mutating action in the same immutable command plan and SHALL validate the active cluster claim before committing.

The bootstrap executor SHALL distinguish a database created by its captured spawn from a database that was already ready. Dry-run, external PostgreSQL, a skipped spawn, readiness failure, missing or pending cluster claim, identity mismatch, and a pre-existing unrecorded `tmp` SHALL NOT append or backfill `bootstrapped`. A catalog-write failure after database creation SHALL fail the operation and leave the database unauthorized for guarded deletion; later readiness SHALL NOT infer ownership.

#### Scenario: Successful bootstrap appends exact event

- **WHEN** the captured bootstrap spawn creates `tmp`, its readiness probe returns `installed`, and the current project cluster exactly matches an active catalog claim
- **THEN** execution appends one `bootstrapped` event with that endpoint, `tmp`, `cluster_id`, and project-owned data directory and no restore provenance

#### Scenario: Existing ready tmp is not adopted

- **WHEN** the initial readiness probe finds an already valid `tmp` but the latest exact event is not `bootstrapped`
- **THEN** the spawn and record action are skipped and no bootstrap evidence is appended or backfilled

#### Scenario: Failed bootstrap creates no authority

- **WHEN** the bootstrap spawn, readiness probe, active-claim validation, or event append fails
- **THEN** initialization fails and no successful `bootstrapped` event is committed

#### Scenario: Preview and external PostgreSQL are inert

- **WHEN** bootstrap is previewed with `--dry-run` or the project uses external PostgreSQL
- **THEN** the public plan remains secret-free and no `database_events` row is written

#### Scenario: Bootstrap event action is inspectable

- **WHEN** Compose initialization or first-run bootstrap is planned
- **THEN** its immutable plan contains the bootstrap spawn, probes, readiness verification, and conditional `bootstrapped` record action that execution consumes without rebuilding process or identity inputs

### Requirement: Stored project addon and server-module inputs

`ProjectConfig` SHALL store ordered project-relative or absolute `addon_roots` and ordered `server_wide_modules` in `.odcli/project.toml`. Absolute addon roots SHALL be accepted outside the project and preserved as absolute manifest inputs. Relative addon roots SHALL remain confined to the project after resolution. Addon roots SHALL retain only the first canonical occurrence. This source-path permission SHALL NOT relax containment or ownership checks for managed filestore, generated configuration, addon repositories, or resource deletion. Module names SHALL reject blank or comma-containing values and SHALL retain first occurrence. On repeated init, an omitted option SHALL retain its stored tuple, while one or more supplied values SHALL replace the corresponding stored tuple.

#### Scenario: Ordered settings round-trip

- **WHEN** init receives addon roots `addons/community`, `addons/private` and server-wide modules `base`, `web`
- **THEN** the manifest round-trips both tuples in supplied order

#### Scenario: Re-init retains omitted settings

- **WHEN** a project already stores addon roots and server-wide modules and repeated init omits both options
- **THEN** init uses the stored values for its resolved manifest and generated config

#### Scenario: Supplied values replace stored settings

- **WHEN** repeated init supplies one or more values for an existing addon-root or server-wide-module tuple
- **THEN** the supplied values replace that complete tuple and drive config regeneration

#### Scenario: Invalid input is rejected before mutation

- **WHEN** a relative addon root escapes the repository, or a module name is blank or contains a comma
- **THEN** init fails before writing the manifest or generated config

### Requirement: Opt-in initialization prepares the primary virtual environment

The public init SDK command and convenience operation SHALL accept an optional primary venv path. When absent, initialization SHALL retain its existing plan, side effects, results, and retry behavior. When present, initialization SHALL resolve the path with the existing primary-preparation rules, require an effective Python executable or uv selector from the existing init input resolution, and capture one immutable plan that writes or verifies the resolved project manifest before reusing the existing primary-venv preparation owner and before any Compose or Odoo bootstrap effect. The validated platform-specific interpreter inside that venv SHALL be persisted as project Python and SHALL be the interpreter captured for later Odoo bootstrap.

#### Scenario: Omitted venv preserves initialization
- **WHEN** an init caller omits the primary venv path
- **THEN** the plan and execution contain no venv preparation steps and retain the existing initialization behavior

#### Scenario: Fresh init captures ordered preparation
- **WHEN** a fresh project is initialized with a primary venv path and an effective Python selector
- **THEN** one immutable plan orders manifest persistence before create-or-validate, dependency preparation, origin/interpreter/lock publication, Compose startup, and Odoo bootstrap

#### Scenario: Missing Python fails before mutation
- **WHEN** a primary venv path is requested but init resolution produces no effective Python executable or uv selector
- **THEN** planning fails with a typed error before writing the manifest, creating the venv, acquiring a mutating lock, or starting Compose or Odoo

#### Scenario: Prepared interpreter drives bootstrap
- **WHEN** integrated preparation succeeds and Compose initialization requires Odoo bootstrap
- **THEN** the captured bootstrap process uses the validated interpreter inside the selected venv rather than the source selector written by the earlier manifest phase

#### Scenario: Empty requirements still prepare the interpreter
- **WHEN** integrated preparation discovers no dependency inputs
- **THEN** the plan creates or validates the venv, publishes trusted origin and the concrete interpreter, omits compile, sync, and dependency-lock publication, and continues to bootstrap

### Requirement: Integrated preparation has phase-consistent retry and failure semantics

Integrated init SHALL retain the standalone preparation operation's locks, validation, process boundary, provenance, staged lock, and atomic persistence guarantees. A failure before preparation publication SHALL NOT persist the target interpreter, trusted origin, or new dependency lock, but SHALL retain the already completed init manifest/config phase and SHALL NOT claim rollback of venv process effects. A failure after successful preparation SHALL retain the prepared state and SHALL prevent successful init completion until remaining Compose/Odoo postconditions pass. Repeating the same opted-in init SHALL verify or repair init artifacts, refresh the selected valid venv, and resume later lifecycle steps without bypassing immutable planning.

#### Scenario: Create or validation failure stops before bootstrap
- **WHEN** venv creation or validation fails during integrated init
- **THEN** init returns failure, later dependency and Compose/Odoo steps do not run, and no target interpreter, trusted origin, or new dependency lock is published

#### Scenario: Compile or sync failure stops before bootstrap
- **WHEN** dependency compilation or synchronization fails during integrated init
- **THEN** init returns failure, the prior manifest remains at its resolved pre-preparation Python state, existing persisted preparation state is restored, and Compose/Odoo steps do not run

#### Scenario: Publication failure restores preparation state
- **WHEN** origin, interpreter, or dependency-lock publication fails after venv processes complete
- **THEN** the preparation transaction restores its previous manifest, origin, and lock bytes and init does not start Compose or Odoo

#### Scenario: Bootstrap failure keeps successful preparation
- **WHEN** preparation publishes successfully but Compose startup or Odoo bootstrap fails
- **THEN** init returns failure while retaining the concrete interpreter, trusted origin, and dependency lock for an identical retry or standalone recovery

#### Scenario: Identical retry refreshes then resumes
- **WHEN** an existing project's requested manifest is identical and init is repeated with the same venv path
- **THEN** init verifies the manifest, validates and refreshes the venv through the captured plan, then executes only the required later lifecycle work

#### Scenario: Damaged existing venv fails closed
- **WHEN** the target path has an interpreter-shaped file but does not validate as a virtual environment
- **THEN** integrated init fails before dependency synchronization, preparation persistence, Compose startup, or Odoo bootstrap

### Requirement: Integrated Python replacement remains explicit and honest

The resolved init Python SHALL be the creation selector for a missing venv. An existing valid venv SHALL retain the standalone refresh contract and SHALL NOT be silently deleted or recreated when the caller changes `--python`; its validated concrete interpreter SHALL remain runtime truth. A changed explicit Python input that changes the requested manifest SHALL retain existing confirmation and `--yes` rules. Plans and results SHALL distinguish the requested selector, venv path, and concrete interpreter without claiming that an existing environment was recreated.

#### Scenario: Missing venv uses changed selector
- **WHEN** a caller selects a missing venv path and supplies a new Python selector
- **THEN** the captured create step passes that selector to uv and the resulting venv interpreter becomes project Python

#### Scenario: Existing venv is not recreated for changed selector
- **WHEN** a caller supplies a changed Python selector with an existing valid selected venv
- **THEN** existing overwrite confirmation applies, preparation refreshes without a create step, and output reports the actual existing venv interpreter

#### Scenario: Headless replacement retains confirmation safety
- **WHEN** a changed Python selector makes an existing manifest differ in no-input or machine mode
- **THEN** init refuses before mutation unless `--yes` is supplied

### Requirement: Generated local administrator password is stable and private

When a self-contained Compose init has no nonblank source or existing generated `admin_passwd`, execution SHALL generate an independent cryptographically random value, write it atomically to the owner-only `0600` generated config, and preserve it across re-init and repair. The value SHALL never appear in the manifest, plan, argv, logs, results, or exceptions.

#### Scenario: Missing local password is generated

- **WHEN** Compose init runs with source and generated configs lacking a nonblank `admin_passwd`
- **THEN** it writes a new private password and uses it for the owned local runtime

#### Scenario: Re-init preserves generated password

- **WHEN** init or generated-config repair runs again
- **THEN** the existing nonblank local password remains unchanged and secret-free in outputs

### Requirement: Image consent remains an immutable init phase

Compose init SHALL capture image resolution and approval as explicit steps in the same immutable init plan, execute them before project/venv writes, and preserve existing approval, dry-run, refusal, and external-mode behavior.

#### Scenario: Consent precedes writes

- **WHEN** an unapproved Compose image is initialized
- **THEN** resolution and consent complete before project configuration or venv mutation

#### Scenario: Dry-run has no approval side effect

- **WHEN** Compose init runs with `--yes --dry-run`
- **THEN** the image steps are visible in the plan but no image pull, prompt, or trust-store write occurs

### Requirement: Explicit external addon sources

Initialization and generated configuration SHALL accept absolute addon roots outside the project checkout. An external root SHALL remain external when a managed worktree is selected. Dependency discovery SHALL collect requirements beside manifests and in their immediate parents within that declared root; a requirements symlink escaping that root SHALL fail before dependency installation.

#### Scenario: Client checkout uses a sibling platform

- **WHEN** init runs in a client Git checkout with absolute addon roots in a sibling platform checkout
- **THEN** its manifest and generated config reference the supplied platform paths and dependency preparation includes their discovered requirements
- **AND** no platform files are copied into or treated as owned by the client checkout

#### Scenario: External dependency escape is rejected

- **WHEN** a discovered requirements file resolves outside its declared absolute addon root
- **THEN** preparation fails before installing dependencies or writing a prepared environment

### Requirement: Initialization reactivates canonical forgotten registration

After every required initialization postcondition succeeds, registration SHALL upsert the canonical existing project row as `registered` without changing `project_id`, duplicating ownership records, adding a dedicated forgotten timestamp, or adopting resources. Dry-run and failed/incomplete initialization SHALL NOT reactivate a forgotten project. Initialization and removal SHALL serialize through the same project lifecycle lock.

#### Scenario: Successful re-init restores visibility

- **WHEN** a forgotten project retains its manifest and a matching `odcli init` completes successfully
- **THEN** the existing row becomes registered, the same full `project_id` returns to catalogue and monitor views, and no ownership row is duplicated

#### Scenario: Failed re-init remains forgotten

- **WHEN** initialization of a forgotten project fails validation, image consent, cluster startup, bootstrap, preparation, or publication
- **THEN** registration remains forgotten and retained resource ownership is unchanged

#### Scenario: Dry-run does not reactivate

- **WHEN** initialization dry-run targets a forgotten canonical project
- **THEN** it may show the planned registration transition but does not change registration state or timestamps
