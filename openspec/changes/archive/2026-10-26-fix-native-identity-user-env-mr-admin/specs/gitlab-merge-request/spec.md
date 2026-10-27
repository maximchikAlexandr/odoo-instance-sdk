## ADDED Requirements

### Requirement: Configured default merge-request assignee
`odcli-multica gitlab mr publish` SHALL derive the canonical catalog setting key from the verified normalized GitLab host and project. A nonblank explicit `--assignee` SHALL override the catalog value; otherwise a present valid integer user ID or exact username value SHALL supply the default; absence SHALL preserve publication without automatic assignment. The selected user SHALL be resolved exactly, SHALL be an active accessible member of the exact project, and SHALL be sent as `assignee_id` for both create and update. The operation SHALL NOT set `reviewer_ids`, use a display-name match, select a different user, or apply this default to core `odcli git mr create`.

#### Scenario: No default preserves current behavior
- **WHEN** publication has no explicit assignee and the exact host/project setting is absent
- **THEN** create or update omits automatic assignee data and otherwise follows the existing MR contract

#### Scenario: Username default assigns create and update
- **WHEN** the exact setting contains an unambiguous active project member username and no explicit override is present
- **THEN** both new and existing matching MR payloads contain that member's numeric `assignee_id`

#### Scenario: Numeric default resolves exact member
- **WHEN** the exact setting contains a positive GitLab user ID for an active accessible member of the exact project
- **THEN** publication validates that exact user and sends the same ID without username or display-name search

#### Scenario: Explicit assignee wins
- **WHEN** `--assignee` supplies an exact username and a different catalog default exists
- **THEN** only the explicit user's verified ID is applied and safe output reports override provenance

#### Scenario: Invalid assignee fails before mutation
- **WHEN** the configured or explicit value is invalid, ambiguous, inactive, inaccessible, not a project member, or cannot be assigned by the publishing credential
- **THEN** publication fails without creating or changing an MR and never falls back to another user

### Requirement: Published assignee is verified and safely observable
After create or update with a selected assignee, OdCLI SHALL require the GitLab response to contain that exact user ID and username in the assignee projection. The frozen result and safe CLI output SHALL contain only the applied assignee ID, username, and explicit/default provenance; tokens, catalog internals, display names, and provider payloads SHALL remain absent. A response mismatch SHALL be a typed provider-consistency failure and SHALL NOT trigger an automatic compensating mutation.

#### Scenario: Provider confirms selected assignee
- **WHEN** GitLab returns the exact selected user after successful create or update
- **THEN** publication succeeds and reports the safe ID/username with its selection source

#### Scenario: Provider returns another or no assignee
- **WHEN** GitLab accepts the request but its response omits or changes the selected assignee
- **THEN** publication reports a bounded consistency failure with the MR identity and performs no retry or reassignment
