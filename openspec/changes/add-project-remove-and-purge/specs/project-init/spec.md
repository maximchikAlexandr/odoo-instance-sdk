## ADDED Requirements

### Requirement: Initialization reactivates canonical forgotten registration

After every required initialization postcondition succeeds, registration SHALL upsert the canonical existing project row as `registered` without changing `project_id`, duplicating ownership records, adding a dedicated forgotten timestamp, or adopting resources. Dry-run and failed/incomplete initialization SHALL NOT reactivate a forgotten project. Initialization and removal SHALL serialize through the same project lifecycle lock.

#### Scenario: Successful re-init restores visibility

- **WHEN** a forgotten project retains its manifest and a matching `odcli init` completes successfully
- **THEN** the existing row becomes registered, the same full `project_id` returns to catalogue and monitor views, and no ownership row is duplicated

#### Scenario: Failed re-init remains forgotten

- **WHEN** initialization of a forgotten project fails validation, image consent, cluster startup, bootstrap, preparation, or publication
- **THEN** registration remains forgotten and retained resource ownership is unchanged

#### Scenario: Dry-run does not reactivate

- **WHEN** initialization dry-run targets a forgotten canonical project
- **THEN** it may show the planned registration transition but does not change registration state or timestamps
