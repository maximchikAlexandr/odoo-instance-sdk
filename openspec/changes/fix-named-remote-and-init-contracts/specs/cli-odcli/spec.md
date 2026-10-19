## ADDED Requirements

### Requirement: Named remote CLI supports deferred database selection

`odcli remote add NAME --url URL --branch GIT_REF` SHALL register a named source without contacting the remote and SHALL accept optional `--database NAME`. `remote update` SHALL preserve the current database, including absence, when only another field changes. `remote add`, `remote update`, and init registration dry-runs SHALL render the same immutable filesystem mutation and SHALL write nothing. Rich, JSON, and TOON projections SHALL represent the nullable database without inventing a value. Public `odcli ps` SHALL load projects containing absent or explicit named-source databases and SHALL leave the manifest unchanged.

#### Scenario: Add without a database

- **WHEN** `odcli remote add lab --url https://lab.example --branch dev` runs for an initialized temporary project
- **THEN** it SHALL succeed offline and the manifest SHALL round-trip `database=None`

#### Scenario: Dry-run is inert

- **WHEN** the same command includes `--dry-run --format json`
- **THEN** it SHALL report the planned manifest change without modifying the manifest or performing HTTP/backup work

#### Scenario: Branch-only update preserves absence

- **WHEN** `remote update lab --branch next` targets a source whose database is absent
- **THEN** it SHALL update only the branch and the database SHALL remain absent

#### Scenario: Process inventory loads optional databases

- **WHEN** `odcli ps --format json` loads projects with respectively absent and explicit named-source databases
- **THEN** both SHALL produce normal inventory output and SHALL not modify either manifest

### Requirement: Init CLI supports optional-database remote forms and image consent

`odcli init` SHALL accept repeatable `--remote NAME URL GIT_REF` and `--remote NAME URL DATABASE GIT_REF` forms and SHALL keep existing option values unambiguous. `--yes` SHALL also confirm the selected Compose image after exact OCI digest resolution. Interactive Rich init without `--yes` SHALL request consent when the resolved digest is not already approved. Resolution and approval SHALL precede project/venv/password writes in the same init plan; dry-run SHALL neither resolve nor approve. Noninteractive init without `--yes` SHALL NOT implicitly approve an image. No new CLI flag or nested CLI operation SHALL be introduced, and `--yes` SHALL NOT waive completeness or other validations.

#### Scenario: Optional named remote in init

- **WHEN** `odcli init --remote lab https://lab.example dev` runs with otherwise complete inputs
- **THEN** init SHALL register lab with no database inside its existing immutable command

#### Scenario: Existing explicit form remains compatible

- **WHEN** `odcli init --remote lab https://lab.example example_db dev` runs
- **THEN** init SHALL preserve `example_db`, and repeated three- and four-field forms SHALL be mixable

#### Scenario: Image approval is part of yes

- **WHEN** Compose init with an unapproved selected digest runs with `--yes`
- **THEN** it SHALL approve that exact digest and continue without bypassing completeness or validation

#### Scenario: Noninteractive mode does not imply consent

- **WHEN** Compose init is noninteractive without `--yes` and the digest lacks approval
- **THEN** it SHALL fail before project or approval mutation

### Requirement: Pinned developer hotfix provenance remains exact

The isolated pinned developer fix tool SHALL accept uv's direct URL form `git+https://...git@SHA` only when the repository identity is the expected source and `vcs_info.commit_id` exactly matches the trusted SHA. It SHALL reject foreign repositories, mutable/unpinned URLs, or mismatched commits before importing the hotfix. The hotfix SHALL keep its own root and SHALL NOT replace the canonical installation.

#### Scenario: Exact uv provenance is accepted

- **WHEN** uv reports the expected repository pinned at the trusted SHA and matching `vcs_info.commit_id`
- **THEN** the verifier SHALL accept the isolated hotfix source

#### Scenario: Foreign or mismatched provenance is rejected

- **WHEN** the URL repository or commit differs from the trusted pin
- **THEN** verification SHALL fail before import and the canonical installation SHALL remain unchanged
