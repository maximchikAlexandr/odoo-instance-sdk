# multica-user-credentials Specification

## Purpose
TBD - created by archiving change extend-odcli-automation-primitives. Update Purpose after archive.
## Requirements
### Requirement: Verified Multica context resolves one root human creator
The existing `odcli-multica` integration SHALL start from its already verified `VerifiedTaskContext` issue and workspace identity, use public typed `multica-py` issue and project reads to follow `parent_id` within that workspace until the root issue, reject cycles, missing parents, cross-workspace parents, and incomplete creator evidence, and require the root creator to be a human member. A parent in another project is valid only when that project's typed workspace identity matches the verified workspace. It SHALL NOT rediscover the checkout binding, duplicate context/preparation/adoption, or persist another binding. The caller SHALL NOT supply a user ID and the resolver SHALL NOT substitute the current agent, workspace owner, machine account, child-issue creator, or issue assignee.

#### Scenario: Child issue resolves its root creator
- **WHEN** the verified task context names a child issue whose finite parent chain ends at a root issue created by a human
- **THEN** the typed context identifies that root issue, workspace, and human creator
- **AND** no Git remote or GitLab request occurs during resolution

#### Scenario: Invalid lineage fails closed
- **WHEN** verified issue/workspace evidence is incomplete, a parent is missing or crosses workspaces, the chain cycles, or the root creator is not a human
- **THEN** resolution fails with a stable sanitized reason before any repository mutation or remote request

### Requirement: Host-scoped GitLab credential mapping
For every credential-dependent bridge operation, `odcli-multica` SHALL resolve the verified root creator and exact normalized GitLab HTTPS host, then invoke the existing core GitLab credential resolver with that member/host pair, the selected project mapping path, the project's existing token dotenv path, and the supplied process environment. A matching encrypted-store record SHALL provide both login mapping and token and SHALL take precedence over every legacy value. Only exact encrypted-record absence SHALL continue to the existing project mapping-file plus token-environment order, including explicit mapping-path and process-environment overrides. A present but invalid encrypted record, encrypted-store failure, missing or ambiguous legacy mapping, empty token, malformed host, or different member/host SHALL fail closed without ambient Git credentials, cross-account fallback, or another lookup chain. Legacy mapping token key names SHALL retain the reserved `ODCLI_GITLAB_TOKEN_` prefix; legacy token files remain owner-only and process environment retains precedence.

#### Scenario: Encrypted-only root creator credentials resolve
- **WHEN** the verified root creator and exact normalized GitLab host have one valid encrypted-store record and no project mapping file or token environment value exists
- **THEN** the bridge returns one private credential snapshot containing that record's login and token for the selected host
- **AND** Git push, native Git passthrough, synchronization, and merge-request publication can consume that same bridge resolution contract

#### Scenario: Encrypted record overrides legacy values
- **WHEN** one valid encrypted record and a conflicting legacy project mapping or token value both match the selected member and host
- **THEN** the bridge uses only the encrypted record's login and token

#### Scenario: Exact absence retains legacy fallback
- **WHEN** the encrypted store is absent or opens successfully without a record for the selected member and host and the existing project mapping plus effective token environment are valid
- **THEN** the bridge resolves the same private credential shape through the unchanged mapping-file and token-environment precedence, honoring explicit overrides

#### Scenario: Broken encrypted record never falls through
- **WHEN** a matching encrypted record or its store is corrupt, unreadable, unavailable, malformed, or missing a required value
- **THEN** the bridge fails before Git or GitLab access and SHALL NOT read a legacy mapping or token

#### Scenario: Another account cannot satisfy lookup
- **WHEN** the encrypted store or legacy mapping contains credentials only for another member or another normalized host
- **THEN** the bridge rejects the operation without invoking Git or GitLab and without exposing a token, token key, or alternate account identity

#### Scenario: Root creator credentials resolve for one host
- **WHEN** the encrypted record is exactly absent and the root creator has one valid legacy mapping for the selected GitLab host and its token key has a non-empty effective value
- **THEN** the resolver returns a typed private credential handle for that host and login without exposing the token in the public result

#### Scenario: Credentials are unavailable
- **WHEN** the encrypted record is exactly absent and the legacy mapping or effective token is absent, empty, ambiguous, or does not match the selected host
- **THEN** the operation fails before invoking Git or GitLab with a sanitized non-secret reason

### Requirement: Secret-free shared identity result
The same immutable resolved user/host context SHALL be consumed by native Git passthrough, `git sync`, and GitLab merge-request publication. Public plans, results, exceptions, logs, fingerprints, repr output, persisted catalog state, URLs, and argv SHALL contain only non-secret user, host, login, workspace, and issue identifiers; token values, credential helper payloads, and authorization headers SHALL remain private and centrally redacted regardless of whether the encrypted or legacy source won.

#### Scenario: One resolution serves all consumers
- **WHEN** Git passthrough, synchronization, and MR publication are planned for the same checkout and host
- **THEN** each consumes the same root-creator and credential resolution contract without asking the agent for a user ID

#### Scenario: Child echoes credential material
- **WHEN** Git, a credential helper, or the GitLab client returns text containing an encrypted-store or legacy token or authorization value
- **THEN** the bounded diagnostic and every public projection replace the secret before publication
