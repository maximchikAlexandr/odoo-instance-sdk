## ADDED Requirements

### Requirement: Named remote sources in project configuration

Project configuration SHALL support multiple named remote entries with normalized URL, explicit database and declared Git branch. Names SHALL match `[a-z][a-z0-9_]*`; invalid or duplicate names and credential-bearing URLs SHALL fail validation. Public SDK and CLI SHALL expose read/add/update/remove operations using the existing manifest. Configuration changes SHALL preserve unrelated values and SHALL NOT contact remote servers or delete operational resources.

#### Scenario: Configure lab and staging

- **WHEN** a project adds lab and staging with different URLs, databases and branches
- **THEN** both entries round-trip independently and can be selected by exact name

#### Scenario: Idempotent and concurrent edits

- **WHEN** the same entry is added again
- **THEN** it is a no-op; a different existing entry requires explicit update
- **AND** a manifest changed since planning causes a conflict without overwriting another edit

#### Scenario: Remove a profile

- **WHEN** an existing named entry is removed
- **THEN** only that configuration entry is removed; backups, secrets and environments remain intact

### Requirement: Initialization accepts named sources

Init SHALL accept multiple typed remote entries in SDK and repeatable `--remote NAME URL DATABASE GIT_REF` in CLI, applying existing validation, no-input, overwrite and dry-run rules. Named entries SHALL satisfy the remote-configuration completeness check without requiring a legacy test entry. Re-init without remote inputs SHALL preserve existing entries. Credential availability SHALL be reported separately; secrets SHALL NOT enter the manifest.

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
