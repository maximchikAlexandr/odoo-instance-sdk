## MODIFIED Requirements

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
## ADDED Requirements

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
