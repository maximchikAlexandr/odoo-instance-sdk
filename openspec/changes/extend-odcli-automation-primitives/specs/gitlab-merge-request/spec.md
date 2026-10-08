## ADDED Requirements

### Requirement: Typed GitLab merge-request publication
The public integration SDK SHALL expose `publish_merge_request_command()` and a delegating convenience method, and the CLI SHALL expose `odcli gitlab mr publish`. The operation SHALL accept source branch, target branch, title, UTF-8 description file, and optional assignee; resolve host/project and the root Multica issue through the shared integration context; append a canonical issue link; and return a frozen result containing MR ID, web URL, source/target branches, and outcome `created` or `updated`.

#### Scenario: Create a merge request
- **WHEN** no open merge request matches the exact GitLab project, source branch, and target branch
- **THEN** the operation creates one MR with the requested title, file contents, issue link, and resolved assignee and returns `created`

#### Scenario: Update the unique merge request
- **WHEN** exactly one open merge request matches the exact project, source branch, and target branch
- **THEN** the operation updates that MR in place and returns its existing ID and URL with outcome `updated`

### Requirement: Unambiguous and secret-safe GitLab effects
MR publication SHALL fail before mutation when project resolution, user credentials, assignee resolution, UTF-8 description reading, or open-MR matching is invalid. More than one matching open MR SHALL be an ambiguity error and SHALL NOT update any MR. GitLab requests SHALL use the root creator's host-scoped token through the shared bounded HTTP transport, SHALL NOT retry non-idempotent create calls automatically, and SHALL redact authorization data and provider payloads.

#### Scenario: Matching merge requests are ambiguous
- **WHEN** two or more open MRs match the exact project and branch pair
- **THEN** publication fails with their non-secret IDs and URLs and changes none of them

#### Scenario: GitLab rejects the request
- **WHEN** the provider returns an authentication, authorization, validation, timeout, or protocol failure
- **THEN** the CLI exits non-zero with a stable typed reason and bounded sanitized diagnostics

### Requirement: MR planning remains inspectable
Command construction SHALL capture local inputs, selected non-secret context, lookup and mutation action steps, and stale-context checks without issuing GitLab requests. Execution SHALL revalidate the root issue, mapped host/project, current branch, and unique open-MR match before mutation.

#### Scenario: Dry-run publication
- **WHEN** MR publication is requested with `--dry-run`
- **THEN** the machine plan shows the selected project, branches, description identity, assignee intent, issue link, lookup, and create-or-update decision boundary
- **AND** no GitLab request or repository mutation occurs
