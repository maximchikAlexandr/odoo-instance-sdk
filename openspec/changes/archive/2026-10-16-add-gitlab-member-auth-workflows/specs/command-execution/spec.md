## ADDED Requirements

### Requirement: Generic GitLab plans use existing effect boundaries

Clone, submodule initialization, explicit-repository commit, sync, identity verification, image upload, and merge-request create/update SHALL expose immutable sanitized command/action plans using existing `Command`, `internal/proc`, and bounded HTTP action conventions. Construction/dry-run SHALL perform no Git, filesystem mutation, upload, or merge-request mutation; execution SHALL revalidate repository identity, origin/host, branch, selected member, token owner, local input identities, and remote decision boundaries before each irreversible effect.

#### Scenario: Preview generic Git mutation
- **WHEN** a new generic Git command is invoked with `--dry-run`
- **THEN** the plan contains ordered sanitized process/action identities and performs no clone, submodule update, commit, fetch, rebase, push, upload, or MR mutation

#### Scenario: Repository changes after planning
- **WHEN** repository root, origin, branch, staged snapshot, description, or image identity differs at execution
- **THEN** the command fails before the dependent mutation and does not rebuild an uninspected plan

### Requirement: Recursive credential boundary is fail-closed

Credentialed Git execution SHALL disable ambient/system/global credential helpers and repository-controlled helper, hook, protocol, and command overrides that could receive selected credentials. The plan SHALL validate the superproject and every resolved recursive submodule host/config before the first credentialed child. Public projections SHALL preserve argument boundaries while redacting private askpass paths, payloads, tokens, authorization values, and secret-derived fingerprints.

#### Scenario: Repository defines a credential helper
- **WHEN** the target repository or clone configuration would invoke an untrusted helper or hook during a credentialed operation
- **THEN** OdCLI rejects the plan before issuing credentials or starting mutation

#### Scenario: Nested submodule introduces a second host
- **WHEN** a recursive submodule graph resolves to two allowed GitLab hosts
- **THEN** OdCLI requires independent exact mappings for the selected member and confines each credential to its own host
