## MODIFIED Requirements

### Requirement: CLI exposes named remote configuration

CLI SHALL expose `remote ls`, `remote add NAME --url URL [--database DB] --branch REF`, `remote update NAME` with the same configurable fields, and `remote remove NAME`. The database option is optional; when omitted, the manifest SHALL omit `database` and refresh/init SHALL resolve it only for the current operation. Add SHALL use the public configure primitive without replace; update SHALL use the same primitive with explicit replace and preserve unspecified fields before constructing the complete typed source. Init SHALL accept repeatable named remotes in both `--remote NAME URL GIT_REF` and `--remote NAME URL DATABASE GIT_REF` forms. Every operation SHALL delegate once to a public SDK primitive; credentials SHALL NOT be accepted as literal CLI arguments.

#### Scenario: Add a remote without a database

- **WHEN** a caller runs `odcli remote add lab --url https://lab.example --branch dev`
- **THEN** the operation is offline, succeeds without contacting the remote, and the persisted entry omits `database`

#### Scenario: Init accepts both named-remote forms

- **WHEN** init receives `--remote lab https://lab.example dev` or `--remote lab https://lab.example chosen_db dev`
- **THEN** it stores respectively an omitted or explicit database while preserving the declared branch and all existing init rules

#### Scenario: Update preserves an omitted database

- **WHEN** a remote without a database is updated without a `--database` value
- **THEN** the update preserves the omission and does not invent a database

#### Scenario: Copy refuses an unresolved named database

- **WHEN** COPY checkout selects a named remote whose database is omitted
- **THEN** it refuses before download or mutation with the supported alternative to resolve the database through `db refresh --remote` or provide `--backup`

#### Scenario: Add staging after init

- **WHEN** a caller adds staging with URL, database and branch
- **THEN** the SDK atomically stores the entry and the CLI reports expected credential variable names without values

#### Scenario: Preview configuration change

- **WHEN** add, update, remove or init uses dry-run
- **THEN** the output describes the configuration change and no configuration or operational resource is mutated

#### Scenario: COPY refuses an unresolved named remote

- **WHEN** COPY checkout selects a named remote whose database is omitted and no explicit backup is supplied
- **THEN** it refuses before download or mutation and identifies `db refresh --remote` or `--backup` as the supported alternative
## ADDED Requirements

### Requirement: Init and remote configuration preserve secret boundaries

Remote registration, init, and refresh SHALL report only derived credential-key names and SHALL never persist, print, or place a master password in a manifest, plan, process argument, or diagnostic projection.

#### Scenario: Offline registration is secret-free

- **WHEN** a named remote is added or updated in dry-run or normal mode
- **THEN** no network request or secret read is performed and output contains no secret value

### Requirement: `ps` preserves optional remote configuration

The `odcli ps` leaf SHALL load and render a project whose named remote omits `database` without inferring, writing, or otherwise mutating that value. Invalid explicit remote database values SHALL remain rejected by the existing project configuration validation, before rendering or mutation.

#### Scenario: ps renders a remote without a database

- **WHEN** `odcli ps --format json` loads a project whose named remote omits `database`
- **THEN** it succeeds, renders the process result, and leaves the project manifest unchanged

#### Scenario: ps rejects invalid explicit database values

- **WHEN** a project contains an invalid explicit remote database value
- **THEN** project configuration validation rejects it before `ps` renders or mutates anything
