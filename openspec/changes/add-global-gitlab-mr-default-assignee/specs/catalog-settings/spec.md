## MODIFIED Requirements

### Requirement: Host/project-scoped MR assignee setting
The default merge-request assignee SHALL accept the exact global key `gitlab.mr.default_assignee` and the exact scoped key form `gitlab.mr.default_assignee:https://<normalized-host>/<normalized-project-path>`. The canonical JSON value for either form SHALL be one positive GitLab numeric user ID or one nonblank exact username without whitespace; booleans, null, non-positive numbers, display names, arrays, and objects SHALL be invalid. Deleting the global row SHALL disable only the global fallback, and deleting one scoped row SHALL remove only that exact host/project override.

#### Scenario: Valid global assignee
- **WHEN** Admin stores a positive integer or exact username at `gitlab.mr.default_assignee`
- **THEN** the catalog preserves its integer-or-string type and makes it available as the single global MR assignee setting

#### Scenario: Valid scoped assignee
- **WHEN** Admin stores a positive integer or exact username at the canonical key for one normalized GitLab host/project
- **THEN** the setting is available only to that exact publication scope and preserves its integer-or-string type

#### Scenario: Invalid assignee setting
- **WHEN** Admin receives another JSON type, an empty or whitespace-containing username, a display name, or a non-positive number for either the global or scoped MR assignee key
- **THEN** it reports a value-field validation error and leaves the prior setting unchanged

#### Scenario: Deleting one scope preserves the other
- **WHEN** Admin deletes the global row or one scoped row while another MR assignee row exists
- **THEN** only the selected row is removed and every other global or scoped row remains unchanged
