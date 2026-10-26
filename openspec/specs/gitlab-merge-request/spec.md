# gitlab-merge-request Specification

## Purpose
TBD - created by archiving change extend-odcli-automation-primitives. Update Purpose after archive.
## Requirements
### Requirement: Typed GitLab merge-request publication
The existing `odcli-multica` public integration SDK SHALL expose `publish_merge_request_command()` and a delegating convenience method, and its CLI SHALL expose `odcli-multica gitlab mr publish`. The operation SHALL accept source branch, target branch, title, UTF-8 description file, optional assignee, and an optional explicit HTTPS issue URL when the client has no configured server URL or uses a loopback HTTP daemon URL; resolve host/project and the root Multica issue through the shared verified task context; append the validated issue link; and return a frozen result containing MR ID, web URL, source/target branches, and outcome `created` or `updated`. An explicit URL SHALL have no credentials, query, or fragment and SHALL end in the verified issue ID. A configured HTTPS server URL SHALL remain authoritative. The core wheel SHALL NOT import this extension operation.

#### Scenario: Local daemon and public issue link
- **WHEN** the verified Multica daemon uses a loopback HTTP URL and the caller supplies an HTTPS issue URL ending in the verified issue ID
- **THEN** the operation uses that URL for the MR link without changing the daemon identity
- **AND** a missing, non-HTTPS, or different-issue URL fails before provider mutation

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

### Requirement: Core repository-scoped merge-request creation

Core `odcli git mr create --repo REPOSITORY --member-id MEMBER_ID --target TARGET --title TITLE --description-file PATH [--image PATH]` SHALL derive the exact GitLab host/project and source branch from the verified repository, read a bounded regular UTF-8 Markdown description file, and create or update the unique open merge request matching that exact project/source/target tuple. The command SHALL return a frozen result with project, MR IID/web URL, source/target, and `created` or `updated`; it SHALL work without an OdCLI project and SHALL NOT import Multica or require an issue context.

#### Scenario: Create MR for selected repository
- **WHEN** no matching open MR exists and all repository, credential, branch, title, and description inputs validate
- **THEN** GitLab creates one MR in the project derived from `--repo` with the exact UTF-8 description

#### Scenario: Update unique matching MR
- **WHEN** exactly one open MR matches the exact project/source/target tuple
- **THEN** OdCLI updates that MR in place and returns its existing IID and URL

#### Scenario: Matching MRs are ambiguous
- **WHEN** more than one open MR matches the tuple
- **THEN** OdCLI fails with bounded non-secret identifiers and changes none of them

### Requirement: Idempotent optional image upload

When `--image` names a bounded regular image file, OdCLI SHALL upload it through GitLab project uploads without adding it to Git, append the returned Markdown link and an OdCLI-owned content-digest marker to the MR description, and preserve user-authored Markdown. A repeated create/update with the same image digest SHALL reuse the existing owned link without another upload or duplicate Markdown; a different digest SHALL upload once and replace only the owned image block. Upload and MR mutation SHALL use ordered action steps and non-idempotent requests SHALL NOT be retried automatically.

#### Scenario: Upload an image once
- **WHEN** a valid image is supplied and its digest marker is absent from the matching description
- **THEN** OdCLI uploads the file once and writes exactly one owned image link/marker block into the created or updated MR

#### Scenario: Repeat with the same image
- **WHEN** the matching MR already contains the OdCLI-owned marker for the supplied image digest
- **THEN** OdCLI performs no upload and keeps exactly one owned image reference

#### Scenario: MR mutation fails after upload
- **WHEN** upload succeeds but create/update fails
- **THEN** OdCLI reports a sanitized possible-orphan upload outcome, does not retry mutation automatically, and does not commit the image

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
