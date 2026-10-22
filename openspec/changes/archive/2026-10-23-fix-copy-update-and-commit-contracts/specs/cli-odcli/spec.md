## ADDED Requirements

### Requirement: Project-aware Git commit accepts verified member selection

`odcli git commit` SHALL accept `--member-id MEMBER_UUID` without `--repo` when the root CLI context resolves an initialized project or registered environment. That form SHALL use project-aware Odoo commit policy, allow `--tag`, preserve root `--project` and `--env UUID` selection, and route the member UUID into the verified host-scoped author/committer environment of the immutable SDK command. `--repo` SHALL continue to require `--member-id` and select explicit-repository mode; `--repo` with `--tag` SHALL remain invalid. `--member-id` SHALL be a Multica member UUID and SHALL NOT be interpreted as a GitLab numeric user ID.

#### Scenario: Project environment commit selects a member

- **WHEN** `odcli --project CORE --env UUID git commit DESCRIPTION --ticket ICODE-91 --tag DOC --member-id MEMBER_UUID --dry-run` resolves an initialized worktree and verified GitLab member
- **THEN** the plan retains the environment checkout, `[DOC] module: ICODE-91 DESCRIPTION`, configured bare ticket URL, and safe verified author/committer name and email without requiring `--repo`

#### Scenario: Project-aware member execution uses the previewed snapshot

- **WHEN** the previewed project-aware member commit is confirmed and its staged tree, worktree identity, and verified identity remain valid
- **THEN** execution runs the same captured `git commit` message and `GIT_AUTHOR_*`/`GIT_COMMITTER_*` values without replanning

#### Scenario: Explicit repository pairing remains strict

- **WHEN** `--repo` is supplied without `--member-id`, or `--repo` and `--tag` are combined
- **THEN** Click returns a usage error before SDK resolution or Git execution

#### Scenario: Member-only mode requires project context

- **WHEN** `--member-id` is supplied without `--repo` but no initialized project or registered environment can be resolved
- **THEN** the command fails before identity lookup or Git execution and does not fall back to generic repository mode
