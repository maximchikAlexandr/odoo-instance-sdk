## ADDED Requirements

### Requirement: Adopt a caller-owned Multica checkout
`EnvironmentResource` SHALL expose an inspectable adoption operation and the CLI SHALL expose `odcli env adopt` for an existing checkout proven by the public Multica integration context. Adoption SHALL register the existing canonical worktree, Git common directory, branch/HEAD, project identity, runtime configuration, database mode/binding, and external owner `multica` without running `git worktree add`, cloning, moving, deleting, or taking filesystem ownership of the checkout. Ordinary environment checkout/create SHALL continue to create and own its managed worktree.

#### Scenario: Adopt a valid Multica checkout
- **WHEN** a caller-owned checkout has an unambiguous Multica issue/workspace binding, a valid project manifest, safe Git identity, and no conflicting active environment
- **THEN** adoption records a ready externally owned environment whose worktree path remains unchanged
- **AND** no Git fetch, checkout, worktree creation, database preparation, or runtime start occurs

#### Scenario: Ambiguous or conflicting adoption
- **WHEN** the checkout is outside the selected project, already owned by another live environment, dirty where captured identity requires clean state, detached, missing its Multica binding, or has mismatched Git evidence
- **THEN** adoption fails before catalog mutation and leaves the caller checkout untouched

### Requirement: Externally owned checkout lifecycle is non-destructive
Sync, run, stop, module, Git, publication, monitor, and database operations MAY consume an adopted environment through the existing owner-neutral runtime contract. Environment removal SHALL stop only its proven runtime, unpublish its route, release OdCLI-owned database/config/catalog artifacts according to existing rules, and unregister the environment, but SHALL NOT delete, move, clean, reset, or run native Git update against the externally owned checkout. Code update for adopted Multica checkouts SHALL occur only through the credential-aware Git wrapper.

#### Scenario: Remove an adopted environment
- **WHEN** an adopted environment is removed after runtime and route cleanup succeeds
- **THEN** its registration and OdCLI-owned artifacts are removed while the checkout and its Git state remain byte-for-byte caller-owned

#### Scenario: Generic environment sync is requested
- **WHEN** `env sync` targets an adopted Multica checkout
- **THEN** it refuses the native managed-worktree update path and directs the caller to the credential-aware Git operation
