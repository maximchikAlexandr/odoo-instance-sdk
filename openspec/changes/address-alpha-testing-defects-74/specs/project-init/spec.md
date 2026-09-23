## MODIFIED Requirements

### Requirement: `ProjectConfig` public type

`ProjectConfig` SHALL make `test_instance.database` optional. The parser and writer SHALL round-trip a config without `database` without adding it. When `database` is explicitly set, it SHALL be used without requiring database list availability. When it is absent, remote-refresh preflight SHALL obtain names through the existing `DatabaseResource.names()` and select exactly one; zero SHALL fail with `remote_database_none`; many SHALL fail with `remote_database_ambiguous` listing names; an unavailable list SHALL fail with `remote_database_list_unavailable`. All three fail before download. The auto-detected name SHALL NOT be written back to `project.toml`.

#### Scenario: optional database round-trips

- **WHEN** a `ProjectConfig` without `test_instance.database` is parsed and written back
- **THEN** the written manifest does not add `database`

#### Scenario: explicit database has priority

- **WHEN** `test_instance.database` is explicitly set
- **THEN** it is used and database list availability is not required

#### Scenario: absent database is resolved from the instance

- **WHEN** `test_instance.database` is absent and the instance exposes exactly one database
- **THEN** that database is selected for the current operation and the name is not written back to `project.toml`

#### Scenario: unavailable list is a distinct error

- **WHEN** `test_instance.database` is absent and the database list is unavailable
- **THEN** the operation fails before download with `remote_database_list_unavailable`

### Requirement: Headless init

`odcli init --no-input` SHALL accept `--test-url`, `--test-database`, `--test-branch`, `--local-config`, and `--allow-partial`. When the setup is incomplete and `--allow-partial` is absent, it SHALL fail with `init_incomplete` before writes and change no manifest, config, dotenv, catalog, or cluster resources. `--yes` SHALL NOT bypass the completeness check. When `--allow-partial` is explicit, partial initialization SHALL proceed and the result SHALL contain a `partial initialization` warning with the missing capabilities. A fully-specified setup SHALL NOT ask any question and SHALL remain automatable. `--dry-run` SHALL NOT prompt.

#### Scenario: no-input incomplete fails without allow-partial

- **WHEN** `odcli init --no-input --yes` runs with an incomplete setup and without `--allow-partial`
- **THEN** it fails with `init_incomplete` before writes and changes nothing

#### Scenario: no-input allow-partial proceeds

- **WHEN** `odcli init --no-input --allow-partial` runs with an incomplete setup
- **THEN** partial initialization proceeds and the result warns about the missing capabilities

#### Scenario: full setup is automatable

- **WHEN** `odcli init --no-input --yes` runs with a fully-specified setup
- **THEN** no question is asked and the project is fully initialized

### Requirement: Compose init produces a usable project runtime config

A full self-contained `init` with `--local-config` and Compose PostgreSQL SHALL produce: an owned Compose cluster, a generated `.odcli/odoo.conf` as the effective local `source_config`, `data_dir` set to the absolute `{project_root}/.odcli/filestore`, a `.odcli/.env` under `0600` with `ODCLI_TEST_INSTANCE_ORIGIN_PINS` and an empty `ODCLI_TEST_MASTER_PASSWORD=`, a valid `[test_instance]` when `--test-url`/`--test-branch` are provided (and `--test-database` or a uniquely listable remote database), and a valid Odoo bootstrap database `tmp` with `base` installed. Master password SHALL NOT be accepted as a CLI argument and SHALL NOT be stored in manifest/history. Re-running `init` without test-instance options SHALL preserve an existing valid `[test_instance]`. Completeness groups follow design D5. Bootstrap `tmp` follows design D5a.

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

### Requirement: Self-contained Compose init creates bootstrap database `tmp`

Self-contained Compose `init` SHALL create Odoo database `tmp` with `base` installed through the same project runtime. Spawn argv SHALL come from `resolve_runtime_argv` / `_build_cli_args` plus `--database tmp --init=base --stop-after-init` as a ProcessStep via `internal/proc` with `shell=False`. A separate Odoo version or second launcher SHALL NOT be introduced. After `--stop-after-init` the process has exited: readiness SHALL be SQL `SELECT state FROM ir_module_module WHERE name = 'base'` on database `tmp` returning `installed` (owned cluster ActionStep). `POST /web/webclient/version_info` SHALL NOT run at `init`. The operation SHALL be idempotent: that SQL success SHALL NOT recreate; missing relation or state not `installed` SHALL fail with `init_bootstrap_failed`. `--dry-run` SHALL show the step and SHALL NOT spawn. A failure SHALL NOT return a successful project-setup result. If an owned Compose project has no valid `tmp`, the first `odcli run` SHALL run the same ProcessStep+SQL.

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
