## MODIFIED Requirements

### Requirement: Verified Multica context resolves one root human creator

The existing `odcli-multica` integration SHALL start from its already verified `VerifiedTaskContext` issue, workspace, checkout, and repository identity. When no explicit member is supplied, it SHALL use public typed `multica-py` issue and project reads to follow `parent_id` within that workspace until the root issue, reject cycles, missing parents, cross-workspace parents, and incomplete creator evidence, and require the root creator to be a human member. A parent in another project is valid only when that project's typed workspace identity matches the verified workspace. When an explicit member UUID is supplied, it SHALL normalize the UUID and require exactly one current human member of that same verified workspace. The result SHALL contain the selected member and source `root_creator` or `explicit`. Neither path SHALL rediscover checkout binding, duplicate context/preparation/adoption, persist another binding, or substitute the current agent, workspace owner, machine account, child-issue creator, issue assignee, cache account, or another member.

#### Scenario: Child issue resolves its root creator

- **WHEN** the verified task context names a child issue whose finite parent chain ends at a root issue created by a human and no explicit member is supplied
- **THEN** the typed selection identifies that root issue, workspace, human creator, and `root_creator` source without a Git remote or GitLab request

#### Scenario: Explicit human workspace member is selected

- **WHEN** `member_id` names exactly one current human member of the verified workspace
- **THEN** the typed selection uses that UUID with `explicit` source while retaining the verified task, checkout, project, workspace, and repository facts

#### Scenario: Explicit member is invalid

- **WHEN** `member_id` is malformed, absent from the workspace, ambiguous, or identifies an agent, squad, service identity, or removed member
- **THEN** selection fails before credential lookup, Git, or GitLab access and does not fall back to the root creator

#### Scenario: Invalid lineage fails closed

- **WHEN** automatic selection sees incomplete verified evidence, a missing or cross-workspace parent, a cycle, or a non-human root creator
- **THEN** resolution fails with a stable sanitized reason before repository mutation or remote request

### Requirement: Host-scoped GitLab credential mapping

For every credential-dependent bridge operation, `odcli-multica` SHALL resolve the shared task-member selection and exact normalized GitLab HTTPS host, then invoke the existing core GitLab credential resolver with that member/host pair, the effective user root, optional explicit legacy paths, and supplied process environment. It SHALL NOT synthesize project `.odcli` mapping or dotenv defaults. A matching encrypted-store record SHALL provide both login mapping and token and SHALL take precedence over every legacy value. Only exact encrypted-record absence SHALL continue through the existing explicit-or-user-root mapping and token order. A present but invalid encrypted record, encrypted-store failure, missing or ambiguous legacy mapping, empty token, malformed host, or different member/host SHALL fail closed without ambient Git credentials, cache-account fallback, cross-account fallback, or another lookup chain.

#### Scenario: Encrypted-only selected credentials resolve

- **WHEN** the selected explicit member or root creator and exact normalized host have one valid encrypted-store record and no project credential files exist
- **THEN** the bridge returns one private credential snapshot for that member/host and Git passthrough, synchronization, and MR publication can consume it

#### Scenario: User-root legacy fallback resolves

- **WHEN** the encrypted record is exactly absent and valid mapping/token data exists only under the effective user root
- **THEN** the bridge resolves that credential without requiring a project-local copy

#### Scenario: Explicit legacy override is honored

- **WHEN** the encrypted record is exactly absent and callers supply valid mapping/environment paths
- **THEN** the bridge uses the core resolver's explicit-path precedence without merging implicit project files

#### Scenario: Broken encrypted record never falls through

- **WHEN** a matching encrypted record or its store is corrupt, unreadable, unavailable, malformed, or missing a required value
- **THEN** the bridge fails before Git or GitLab access and SHALL NOT read a legacy mapping or token

#### Scenario: Another account cannot satisfy lookup

- **WHEN** storage contains credentials only for another member or normalized host
- **THEN** the bridge rejects the operation without invoking Git or GitLab and without exposing a token, token key, or alternate identity

### Requirement: Secret-free shared identity result

The same immutable selected member/host context SHALL be consumed by native Git passthrough, `git sync`, and GitLab merge-request publication. Public plans and results SHALL report the selected Multica member UUID, normalized host, login, and source `root_creator` or `explicit`. Public exceptions, logs, fingerprints, repr output, persisted catalog state, URLs, argv, and binding files SHALL contain no token, credential-helper payload, authorization header, or secret-derived value regardless of whether encrypted or legacy storage won.

#### Scenario: One selection serves all consumers

- **WHEN** Git passthrough, synchronization, and MR publication are planned for the same verified checkout, selected member, and host
- **THEN** each consumes the same selection and credential contract without callback-local identity logic

#### Scenario: Explicit selection is inspectable

- **WHEN** a caller supplies `member_id`
- **THEN** preview and success output identify only that member UUID, safe login/host, and `explicit` source

#### Scenario: Child echoes credential material

- **WHEN** Git, a credential helper, or the GitLab client returns text containing an encrypted-store or legacy token or authorization value
- **THEN** the bounded diagnostic and every public projection replace the secret before publication
