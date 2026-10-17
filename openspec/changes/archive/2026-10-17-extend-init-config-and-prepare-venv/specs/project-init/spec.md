## ADDED Requirements

### Requirement: Stored project addon and server-module inputs

`ProjectConfig` SHALL store ordered project-relative `addon_roots` and ordered `server_wide_modules` in `.odcli/project.toml`. Addon roots SHALL reject absolute paths and paths that escape the repository root after resolution and SHALL retain only the first canonical occurrence. Module names SHALL reject blank or comma-containing values and SHALL retain first occurrence. On repeated init, an omitted option SHALL retain its stored tuple, while one or more supplied values SHALL replace the corresponding stored tuple.

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

- **WHEN** an addon root is absolute or escapes the repository, or a module name is blank or contains a comma
- **THEN** init fails before writing the manifest or generated config

## MODIFIED Requirements

### Requirement: Compose init produces a usable project runtime config

A full self-contained `init` with `--local-config` and Compose PostgreSQL SHALL produce: an owned Compose cluster, a generated `.odcli/odoo.conf` as the effective local `source_config`, `data_dir` set to the absolute `{project_root}/.odcli/filestore`, ordered absolute `addons_path` values resolved from stored addon roots, stored `server_wide_modules`, a `.odcli/.env` under `0600` with `ODCLI_TEST_INSTANCE_ORIGIN_PINS` and an empty `ODCLI_TEST_MASTER_PASSWORD=`, a valid `[test_instance]` when `--test-url`/`--test-branch` are provided (and `--test-database` or a uniquely listable remote database), and a valid Odoo bootstrap database `tmp` with `base` installed. The existing generator SHALL accept an absent source config, preserve applicable source options when one exists, and overlay project-owned runtime values. Master password SHALL NOT be accepted as a CLI argument and SHALL NOT be stored in manifest/history. Re-running `init` without test-instance, addon-root, or server-wide-module options SHALL preserve existing valid values. Completeness groups follow design D5. Bootstrap `tmp` follows design D5a.

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
