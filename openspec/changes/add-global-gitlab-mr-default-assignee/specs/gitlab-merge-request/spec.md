## MODIFIED Requirements

### Requirement: Configured default merge-request assignee
`odcli-multica gitlab mr publish` SHALL derive both the canonical exact-project catalog key from the verified normalized GitLab host/project and the exact global key `gitlab.mr.default_assignee`. It SHALL select the first applicable value in this order: nonblank explicit `--assignee`, present exact-project setting, present global setting, then no automatic assignee. A present invalid higher-priority value SHALL fail before mutation and SHALL NOT fall through to a lower-priority value. The selected positive integer user ID or exact username SHALL be resolved exactly, SHALL identify an active accessible member of the exact project, and SHALL be sent as `assignee_id` for both create and update. The operation SHALL NOT set `reviewer_ids`, use a display-name match, select a different user, or apply either default to core `odcli git mr create`.

#### Scenario: No default preserves current behavior
- **WHEN** publication has no nonblank explicit assignee and both the exact-project and global settings are absent
- **THEN** create or update omits automatic assignee data and otherwise follows the existing MR contract

#### Scenario: Project-specific default wins over global default
- **WHEN** both settings exist and no nonblank explicit assignee is present
- **THEN** publication reads and applies only the exact-project value and does not select the global value

#### Scenario: Global default fills an unconfigured project
- **WHEN** the exact-project setting is absent, the global setting contains a valid user ID or exact username, and no nonblank explicit assignee is present
- **THEN** publication resolves that global value and sends the verified project member's numeric `assignee_id`

#### Scenario: Explicit assignee wins over both defaults
- **WHEN** `--assignee` supplies an exact username or numeric ID while project-specific and global settings exist
- **THEN** publication applies only the explicit user and does not read or select either catalog setting

#### Scenario: Numeric default resolves exact member
- **WHEN** the selected project-specific or global setting contains a positive GitLab user ID for an active accessible member of the exact project
- **THEN** publication validates that exact user and sends the same ID without username or display-name search

#### Scenario: Invalid selected value fails without fallback
- **WHEN** the explicit, exact-project, or global value selected by precedence is invalid, ambiguous, inactive, inaccessible, not a project member, or cannot be assigned by the publishing credential
- **THEN** publication fails without creating or changing an MR and never selects a lower-priority user
