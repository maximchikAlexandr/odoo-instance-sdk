## MODIFIED Requirements

### Requirement: Named remote sources in project configuration

Project configuration SHALL support multiple named remote entries with normalized URL, optional database and declared Git branch. Absence of a database SHALL be valid; an explicit value SHALL be a nonblank string. Names SHALL match `[a-z][a-z0-9_]*`; invalid or duplicate names, unknown fields, blank/non-string explicit databases, and credential-bearing URLs SHALL fail validation. Serialization SHALL omit the database line when absent and preserve it when explicit. The public project-init surface SHALL expose listing, one immutable configure operation with explicit replace semantics, and immutable removal using the existing manifest; no additional client facade SHALL be introduced. Configuration commands SHALL capture canonical repository identity and manifest fingerprint, take the existing project lock, reject drift, preserve unrelated project values, and SHALL NOT contact remote servers or delete secrets or operational resources.

#### Scenario: Configure sources with and without a database

- **WHEN** a project adds lab without a database and staging with an explicit database
- **THEN** both entries SHALL round-trip independently, lab SHALL omit the TOML database key, and staging SHALL preserve its exact database

#### Scenario: Invalid explicit database is rejected

- **WHEN** a named source contains an empty, whitespace, or non-string database value
- **THEN** loading or configuration SHALL fail before manifest mutation

#### Scenario: Idempotent and concurrent edits

- **WHEN** the same entry is added again
- **THEN** it SHALL be a no-op; a different existing entry SHALL require explicit update
- **AND** a manifest changed since planning SHALL cause a conflict without overwriting another edit

#### Scenario: Remove a profile

- **WHEN** an existing named entry is removed
- **THEN** only that configuration entry SHALL be removed; backups, secrets and environments SHALL remain intact

### Requirement: Initialization accepts named sources

Init SHALL accept multiple typed remote entries in SDK and repeatable CLI forms `--remote NAME URL GIT_REF` and `--remote NAME URL DATABASE GIT_REF`, including `--remote=NAME`, and SHALL apply existing validation, no-input, overwrite and dry-run rules. Three-field entries SHALL persist an absent database; four-field entries SHALL preserve the explicit database. Mixed forms SHALL be allowed in one invocation without consuming unrelated option values, including a literal `--remote` belonging to `--run-arg`. Named entries SHALL satisfy the remote-configuration completeness check without requiring a legacy test entry. Re-init SHALL merge explicitly supplied source names over the existing set, preserve every other named source, and preserve all sources when no remote input is supplied. Credential availability SHALL be reported only as derived `ODCLI_REMOTE_<UPPER_NAME>_MASTER_PASSWORD` key names; secrets SHALL NOT be accepted by init, enter the manifest, or be written to `.odcli/.env`. Registration SHALL remain inside the one init snapshot and SHALL NOT query, back up, or restore a remote.

#### Scenario: Headless initialization with mixed source forms

- **WHEN** complete local init options include one three-field and one four-field named source in no-input mode
- **THEN** init SHALL write both in one mutation, with one absent and one explicit database, without prompting for a legacy source or password

#### Scenario: Re-init merges only supplied names

- **WHEN** re-init updates one named source while other sources already exist
- **THEN** the supplied name SHALL be added or replaced and every other configured source SHALL be preserved

#### Scenario: Preserve all existing sources

- **WHEN** init is repeated without remote options
- **THEN** it SHALL retain every named and legacy remote entry

#### Scenario: Dry-run and invalid inputs are inert

- **WHEN** init previews remote additions or receives conflicting duplicate names
- **THEN** dry-run SHALL perform no filesystem/network mutation and conflicting inputs SHALL fail before any init mutation

## ADDED Requirements

### Requirement: Compose init maintains a local Database Manager password

Fresh Compose init with local generated configuration SHALL produce a nonblank `admin_passwd` usable by subsequent database restore. Execution SHALL preserve a nonblank value from source configuration, otherwise preserve a nonblank value from the existing generated config, otherwise generate a distinct cryptographically random value inside the generated-config write action. Re-init and repair SHALL retain the effective value; missing or blank legacy generated values SHALL be repaired. The source configuration SHALL remain byte-for-byte unchanged. Dry-run SHALL not generate a secret. The value SHALL NOT be accepted as a CLI argument or appear in manifest, command snapshots, Rich/JSON/TOON output, logs, errors, repr, or fingerprints.

#### Scenario: Fresh local config gets a generated password

- **WHEN** Compose init has no nonblank source or generated `admin_passwd`
- **THEN** execution SHALL generate and write one only to the owner-only generated config before bootstrap and later restore SHALL be able to read it

#### Scenario: Existing value is preserved

- **WHEN** source config or an existing generated config supplies a nonblank `admin_passwd`
- **THEN** re-init SHALL retain that value and SHALL NOT generate a replacement

#### Scenario: Legacy blank value is repaired

- **WHEN** the generated config exists with a missing or blank `admin_passwd`
- **THEN** re-init SHALL repair it atomically while preserving the manifest and imported source bytes

#### Scenario: Dry-run and output do not reveal a secret

- **WHEN** init is planned or rendered in any supported output mode
- **THEN** no password SHALL be generated, persisted, or projected

### Requirement: Compose init captures exact PostgreSQL image consent

Compose init SHALL resolve the selected PostgreSQL image to an exact OCI RepoDigest and, when not already approved in the existing per-project user-data store, SHALL capture one approval action before project, virtual-environment, password, cluster, or bootstrap writes. `--yes` SHALL confirm that selected reference and digest; interactive Rich mode without `--yes` SHALL display both and request consent; noninteractive init without `--yes` SHALL fail closed. Matching approval SHALL skip the prompt/write. Refusal or failed resolution SHALL preserve project files and the prior approval. Dry-run SHALL perform no resolution, prompt, or approval write. External PostgreSQL SHALL have no image-consent step. `--yes` SHALL NOT bypass completeness or other validation.

#### Scenario: Yes approves and continues

- **WHEN** Compose init uses `--yes` and the selected digest is not already approved
- **THEN** it SHALL persist that exact approval through the existing store and continue the same immutable init operation

#### Scenario: Interactive consent can be refused

- **WHEN** interactive Rich init displays an unapproved reference and digest and the user declines
- **THEN** init SHALL fail before any project or approval mutation

#### Scenario: Existing approval is reused

- **WHEN** the stored approval matches the newly resolved digest
- **THEN** init SHALL skip the approval prompt and write while retaining fail-closed startup rechecks

#### Scenario: Dry-run and external mode omit consent effects

- **WHEN** init is a dry-run or uses external PostgreSQL
- **THEN** it SHALL perform no image resolution, prompt, or approval mutation
