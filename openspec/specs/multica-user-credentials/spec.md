# multica-user-credentials Specification

## Purpose
TBD - created by archiving change extend-odcli-automation-primitives. Update Purpose after archive.
## Requirements
### Requirement: Verified Multica context resolves one root human creator
The existing `odcli-multica` integration SHALL start from its already verified `VerifiedTaskContext` issue and workspace identity, use only public typed `multica-py` issue reads to follow `parent_id` within that workspace until the root issue, reject cycles, missing parents, cross-workspace parents, and incomplete creator evidence, and require the root creator to be a human member. It SHALL NOT rediscover the checkout binding, duplicate context/preparation/adoption, or persist another binding. The caller SHALL NOT supply a user ID and the resolver SHALL NOT substitute the current agent, workspace owner, machine account, child-issue creator, or issue assignee.

#### Scenario: Child issue resolves its root creator
- **WHEN** the verified task context names a child issue whose finite parent chain ends at a root issue created by a human
- **THEN** the typed context identifies that root issue, workspace, and human creator
- **AND** no Git remote or GitLab request occurs during resolution

#### Scenario: Invalid lineage fails closed
- **WHEN** verified issue/workspace evidence is incomplete, a parent is missing or crosses workspaces, the chain cycles, or the root creator is not a human
- **THEN** resolution fails with a stable sanitized reason before any repository mutation or remote request

### Requirement: Host-scoped GitLab credential mapping
Non-secret integration configuration SHALL map an exact Multica user ID to one or more exact GitLab HTTPS hosts, logins, and token environment-key names. Tokens SHALL be loaded through the existing owner-only project dotenv with process-environment precedence, and token key names SHALL use the reserved `ODCLI_GITLAB_TOKEN_` prefix. Resolution SHALL reject missing, empty, duplicate, malformed, or host-ambiguous mappings and SHALL NOT fall back to ambient Git credentials or another mapped user.

#### Scenario: Root creator credentials resolve for one host
- **WHEN** the root creator has one valid mapping for the selected GitLab host and its configured token key has a non-empty effective value
- **THEN** the resolver returns a typed private credential handle for that host and login without exposing the token in the public result

#### Scenario: Credentials are unavailable
- **WHEN** the user mapping or token is absent, empty, ambiguous, or does not match the selected host
- **THEN** the operation fails before invoking Git or GitLab and identifies only the missing mapping or key name

### Requirement: Secret-free shared identity result
The same immutable resolved user/host context SHALL be consumed by native Git passthrough, `git sync`, and GitLab merge-request publication. Public plans, results, exceptions, logs, fingerprints, repr output, and persisted catalog state SHALL contain only non-secret user, host, login, workspace, and issue identifiers; token values and credential helper payloads SHALL remain private and centrally redacted.

#### Scenario: One resolution serves all consumers
- **WHEN** Git passthrough, synchronization, and MR publication are planned for the same checkout and host
- **THEN** each consumes the same root-creator and credential mapping contract without asking the agent for a user ID

#### Scenario: Child echoes credential material
- **WHEN** Git, a credential helper, or the GitLab client returns text containing a token or authorization value
- **THEN** the bounded diagnostic and every public projection replace the secret before publication
