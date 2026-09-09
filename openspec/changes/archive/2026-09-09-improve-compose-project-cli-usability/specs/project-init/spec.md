## MODIFIED Requirements

### Requirement: Idempotent init

An existing non-identical manifest MUST NEVER be overwritten silently. Identical init MUST be a no-op. `--yes` SHALL be the only non-interactive overwrite confirmation and SHALL work with `--no-input`; without it, non-identical headless or machine init SHALL retain the stable error `manifest exists and differs; remove it first or adjust options`. Interactive Rich init without `--yes` SHALL retain the Click confirmation prompt. `--yes` SHALL NOT weaken validation or make `--dry-run` mutating.

#### Scenario: Identical re-init

- **WHEN** `odcli init` is run with options identical to the existing manifest
- **THEN** it is a no-op and the manifest is unchanged

#### Scenario: Interactive overwrite prompt

- **WHEN** interactive Rich `odcli init` resolves a manifest different from the existing manifest and `--yes` is absent
- **THEN** it asks for Click overwrite confirmation and performs no silent overwrite

#### Scenario: Non-identical existing manifest — TTY prompt

- **WHEN** `odcli init` runs in a TTY with options different from the existing manifest and `--yes` is absent
- **THEN** Click requests overwrite confirmation and no silent overwrite occurs

#### Scenario: Headless overwrite remains refused by default

- **WHEN** `odcli init --no-input` resolves a different manifest without `--yes`
- **THEN** it emits the stable refusal, does not prompt, and does not overwrite

#### Scenario: Non-identical existing manifest — no-input error

- **WHEN** `odcli init --no-input` resolves options different from the existing manifest without `--yes`
- **THEN** it emits `manifest exists and differs; remove it first or adjust options`, does not prompt, and does not overwrite

#### Scenario: Explicit headless overwrite

- **WHEN** `odcli init --no-input --yes` resolves a valid manifest different from the existing manifest
- **THEN** it atomically replaces the generated init artifacts without a prompt

#### Scenario: Confirmed dry-run remains inert

- **WHEN** `odcli init --no-input --yes --dry-run` resolves a different manifest
- **THEN** it reports the same resolved plan without changing any file or catalogue record

### Requirement: Project-local secret-file hygiene

Project initialization SHALL ensure `.odcli/.env` is ignored by an ignore file stored under `.odcli`; it SHALL NOT create, append to, replace, or otherwise modify the repository-root `.gitignore`. Documentation SHALL require owner-only readability. Loading an existing file with group/other permission bits SHALL fail closed with a path-only remediation message. Secret keys and values SHALL be excluded from Rich, JSON, TOON, dry-run plans, errors, diagnostics, logs, and fingerprints.

#### Scenario: Init leaves root ignore unchanged

- **WHEN** init creates or updates local SDK artifacts in a repository with or without a root `.gitignore`
- **THEN** `.odcli/.env` is covered by `.odcli`-local ignore rules and the root `.gitignore` bytes and metadata are unchanged

#### Scenario: Insecure permissions are refused

- **WHEN** `.odcli/.env` is readable or writable by group or others on a platform supporting POSIX mode bits
- **THEN** loading fails before use and advises owner-only permissions without revealing contents

## ADDED Requirements

### Requirement: Compose init produces a usable project runtime config

For `postgres.mode = "compose"`, successful non-preview initialization SHALL create or refresh one project-owned generated Odoo config under `.odcli` through the existing config-generation path. It SHALL preserve the imported `source_config` unchanged, retain its unrelated Odoo options, bind database host, port, user, and password to the owned Compose cluster, apply `preferred_http_port` when present, and use the configured project database. The cluster password SHALL be read from the existing owner-only Compose secret artifact and SHALL appear in neither `project.toml` nor any output, diagnostic, plan projection, or fingerprint. Generated config and secret artifacts SHALL remain owner-only.

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
