## ADDED Requirements

### Requirement: Generic authenticated clone and safe retry

`odcli git clone --member-id MEMBER_ID [--recurse-submodules] URL DESTINATION` SHALL operate without `odcli init`, accept only an allowed absolute GitLab HTTPS repository URL without userinfo/query/fragment, verify the selected member credential before Git, and clone through `internal/proc`. If the destination already contains a Git worktree whose normalized origin matches URL, the command SHALL be idempotent: it SHALL NOT reclone, reset, switch, pull, or change the current branch. With `--recurse-submodules`, it SHALL initialize only unpopulated submodules recursively after validating all resolved submodule URLs and credential hosts.

#### Scenario: Clone a standalone repository
- **WHEN** the destination is absent and the selected member owns valid credentials for URL's host
- **THEN** OdCLI clones the exact URL into the exact destination and the origin remains credential-free

#### Scenario: Retry a recursive clone
- **WHEN** the destination already has the matching origin, a local feature branch is checked out, and one declared submodule is unpopulated
- **THEN** OdCLI preserves the branch and worktree state and initializes only the missing submodule hierarchy

#### Scenario: Existing destination has another origin
- **WHEN** the destination is a repository whose normalized origin differs from URL
- **THEN** OdCLI fails before fetch, checkout, submodule update, or credential delivery

#### Scenario: Submodule host is unapproved
- **WHEN** any resolved recursive submodule URL lacks an exact mapping for the selected member or violates transport policy
- **THEN** OdCLI fails before starting clone/submodule mutation and does not expose a credential to that host

### Requirement: Explicit generic repository commit

`odcli git commit --repo REPOSITORY --member-id MEMBER_ID DESCRIPTION [--ticket TICKET] --yes` SHALL accept any canonical Git root with a verified allowed GitLab origin, require a non-protected attached branch and staged changes, and set explicit `GIT_AUTHOR_*` and `GIT_COMMITTER_*` values from the verified token owner for the captured Git child. For a repository not resolved as an initialized OdCLI project, the subject SHALL be `TICKET: DESCRIPTION` when a ticket is provided and otherwise DESCRIPTION; Odoo tag/module formatting SHALL NOT be imposed. For an initialized Odoo project/worktree, the existing Odoo commit policy SHALL remain authoritative.

#### Scenario: Commit in an independent repository
- **WHEN** staged changes exist in `--repo`, the branch is safe, and the repository is not an initialized OdCLI project
- **THEN** one commit is created with the repository-neutral subject and verified author/committer identity

#### Scenario: Commit in an initialized Odoo project
- **WHEN** `--repo` resolves to the selected initialized Odoo project/worktree
- **THEN** the existing Odoo scope, tag, ticket-link, and history policy remains in force while verified identity is explicit

#### Scenario: Repository has only unstaged changes
- **WHEN** no staged path exists
- **THEN** OdCLI fails before commit and does not auto-stage any file

### Requirement: Explicit HTTPS synchronization

`odcli git sync --repo REPOSITORY --member-id MEMBER_ID --base REF [--push] --yes` SHALL generalize the existing inspect/fetch/rebase/same-name publication state machine to any canonical Git root with an allowed HTTPS GitLab origin. It SHALL reject dirty, detached, protected, mismatched-upstream, changed-origin, stale-remote, or ambiguous states; fetch and push SHALL use only the verified selected-member credential. `--push` SHALL publish only `HEAD:refs/heads/<same-name>` using fast-forward or the exact fetched-SHA lease, and no standalone push command SHALL be added.

#### Scenario: Sync an independent feature branch
- **WHEN** an independent repository has a clean attached feature branch and the selected member can access its verified HTTPS origin
- **THEN** OdCLI fetches, rebases onto the fetched base, and optionally publishes the same branch under the existing lease rules

#### Scenario: Remote changes after planning
- **WHEN** remote branch or origin evidence differs at execution from the frozen plan
- **THEN** OdCLI fails without retrying, rebasing against another snapshot, or pushing

#### Scenario: Synchronize a submodule checkout
- **WHEN** `--repo` points to a superproject submodule checkout
- **THEN** OdCLI synchronizes only that repository and SHALL NOT stage, commit, or publish the parent superproject gitlink
