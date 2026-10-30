## ADDED Requirements

### Requirement: Shared tracker-neutral ticket branch allocation

The SDK SHALL own one tracker-neutral ticket branch allocator used by both environment checkout and current-branch rename. Given a validated ticket, current repository identity, local heads, matching historical OdCLI catalogue rows, and current matching `origin` heads, the allocator SHALL select the unsuffixed ticket when no iteration exists and otherwise one greater than the maximum observed valid iteration. It SHALL recognize only the exact ticket or a positive decimal `_N` suffix, SHALL NOT reuse gaps, and SHALL preserve the existing environment checkout behavior.

#### Scenario: First ticket branch
- **WHEN** no local, catalogue, or `origin` head matches the requested ticket family
- **THEN** the allocator selects the unsuffixed ticket

#### Scenario: Maximum observed iteration wins
- **WHEN** the matching evidence contains the unsuffixed ticket and any combination of suffixed iterations with gaps
- **THEN** the allocator selects one greater than the maximum valid observed suffix and does not reuse a gap

#### Scenario: Evidence source is unavailable
- **WHEN** local refs, matching catalogue history, or current `origin` heads cannot be inspected successfully
- **THEN** allocation fails with an actionable typed error and does not treat the unavailable source as empty

#### Scenario: Existing checkout allocation remains stable
- **WHEN** `env create` or `env checkout` allocates a ticket branch after the shared allocator is introduced
- **THEN** it preserves the existing unsuffixed/`_N`, evidence, provenance, and stale-collision behavior

### Requirement: Safe current branch rename

`GitResource` SHALL expose `rename_branch_command(ticket)` returning one immutable inspectable command and a delegating `rename_branch(ticket)` convenience operation. The operation SHALL target only the selected project's or environment's current branch, SHALL preserve its HEAD commit, index, staged and unstaged changes, untracked files, and working directory, and SHALL return a frozen JSON-safe result containing the previous and selected branch names plus whether a rename occurred.

If the current branch already equals the ticket or a valid positive `_N` member of that ticket family, the operation SHALL return that branch unchanged without allocating a later suffix. Otherwise it SHALL allocate through the shared allocator and perform one non-force rename only after revalidating current branch, HEAD, and selected-name absence against local refs, catalogue history, and current `origin` heads. Index, staged, unstaged, and untracked state SHALL remain unchanged but SHALL NOT prevent rename merely because it is dirty.

#### Scenario: Rename to a free ticket name
- **WHEN** the current branch is attached, does not belong to the requested ticket family, every evidence source is available, and the unsuffixed ticket is free at revalidation
- **THEN** the operation renames only the current branch to the ticket and returns the previous and selected names while preserving HEAD and all checkout state

#### Scenario: Current branch is already in the ticket family
- **WHEN** the current branch is exactly the requested ticket or has a valid positive `_N` suffix for it
- **THEN** the operation returns that same name with `renamed = false` and performs no Git or catalogue mutation

#### Scenario: Detached HEAD
- **WHEN** the selected checkout has no attached current branch
- **THEN** planning fails with an actionable typed error before mutation

#### Scenario: Collision appears after planning
- **WHEN** the selected local, catalogue, or `origin` name becomes occupied before execution
- **THEN** execution raises the established stale-plan failure without overwriting, switching to, deleting, or force-renaming any branch

#### Scenario: Rename precondition changes after planning
- **WHEN** current branch or HEAD differs from the immutable planning snapshot at execution
- **THEN** execution fails before rename and preserves the changed checkout state

### Requirement: Rename is local-only and preserves standard branch configuration

Branch rename SHALL use standard non-force `git branch -m` semantics, including moving the renamed branch's local configuration and retaining its configured upstream relation. It SHALL NOT add a cleanup or compensation phase, create a worktree, database, environment, commit, or catalogue row; SHALL NOT push, fetch, create, delete, or rename a remote branch; and SHALL NOT contact Jira or Multica.

#### Scenario: Renamed branch has local configuration
- **WHEN** the current branch has local branch configuration or an upstream before rename
- **THEN** Git's standard rename behavior moves that local configuration to the selected branch without changing the remote ref

#### Scenario: No remote mutation
- **WHEN** rename succeeds or returns an already-matching no-op
- **THEN** no push/fetch/remote-ref mutation, external ticket lookup, worktree creation, environment creation, or catalogue write occurs
