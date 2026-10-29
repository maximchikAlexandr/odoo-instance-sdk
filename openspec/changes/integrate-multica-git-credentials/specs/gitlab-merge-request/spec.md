## MODIFIED Requirements

### Requirement: Typed GitLab merge-request publication

The existing `odcli-multica` public integration SDK SHALL expose `publish_merge_request_command()` and a delegating convenience method, and its CLI SHALL expose `odcli-multica gitlab mr publish`. The operation SHALL accept source branch, target branch, title, UTF-8 description file, optional assignee, optional explicit credential `member_id`, and an optional explicit HTTPS issue URL when the client has no configured server URL or uses a loopback HTTP daemon URL; resolve host/project and the root Multica issue through the shared verified task context; append the validated issue link; and return a frozen result containing MR ID, web URL, source/target branches, selected non-secret credential identity/source, and outcome `created` or `updated`. An explicit URL SHALL have no credentials, query, or fragment and SHALL end in the verified issue ID. A configured HTTPS server URL SHALL remain authoritative. The core wheel SHALL NOT import this extension operation.

#### Scenario: Local daemon and public issue link

- **WHEN** the verified Multica daemon uses a loopback HTTP URL and the caller supplies an HTTPS issue URL ending in the verified issue ID
- **THEN** the operation uses that URL for the MR link without changing the daemon identity
- **AND** a missing, non-HTTPS, or different-issue URL fails before provider mutation

#### Scenario: Create a merge request

- **WHEN** no open merge request matches the exact GitLab project, source branch, and target branch
- **THEN** the operation creates one MR with the requested title, file contents, issue link, resolved assignee, and selected credential identity and returns `created`

#### Scenario: Update the unique merge request

- **WHEN** exactly one open merge request matches the exact project and branch pair
- **THEN** the operation updates that MR in place and returns its existing ID and URL with outcome `updated`

#### Scenario: Explicit publishing member

- **WHEN** publication supplies a valid explicit `member_id`
- **THEN** provider lookup and mutation use that verified workspace member's exact-host credential and report `explicit` selection without changing issue-link or assignee selection

### Requirement: Unambiguous and secret-safe GitLab effects

MR publication SHALL fail before mutation when project resolution, selected task-member credentials, assignee resolution, UTF-8 description reading, or open-MR matching is invalid. More than one matching open MR SHALL be an ambiguity error and SHALL NOT update any MR. GitLab requests SHALL use the selected task member's host-scoped token through the shared bounded HTTP transport, SHALL NOT fall back to a root creator, cache account, or another member after explicit selection, SHALL NOT retry non-idempotent create calls automatically, and SHALL redact authorization data and provider payloads.

#### Scenario: Matching merge requests are ambiguous

- **WHEN** two or more open MRs match the exact project and branch pair
- **THEN** publication fails with their non-secret IDs and URLs and changes none of them

#### Scenario: Selected credential is unavailable

- **WHEN** the explicit member or automatic root creator has no valid credential for the exact project host
- **THEN** publication fails before lookup or mutation and never uses the cache credential or another account

#### Scenario: GitLab rejects the request

- **WHEN** the provider returns an authentication, authorization, validation, timeout, or protocol failure
- **THEN** the CLI exits non-zero with a stable typed reason and bounded sanitized diagnostics
