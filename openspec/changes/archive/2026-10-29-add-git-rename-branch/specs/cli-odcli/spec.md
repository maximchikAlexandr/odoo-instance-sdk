## ADDED Requirements

### Requirement: Ticket-based branch rename CLI leaf

The existing `odcli git` group SHALL register `rename-branch TICKET` as a bounded `mutating-or-spawning` leaf. It SHALL use the existing root `--project` and `--env` selectors and exact cwd resolution, delegate only to `GitResource.rename_branch_command()`, accept the established `--dry-run` and Rich/JSON/TOON output options, and SHALL NOT introduce a new top-level group, a Jira/Multica selector, a repository-only parallel implementation, or a second command authority.

#### Scenario: Project selection
- **WHEN** a caller runs `odcli --project PATH git rename-branch ICODE-91`
- **THEN** the command resolves that registered project, delegates the validated ticket to the public SDK primitive, and reports the previous and selected branch names

#### Scenario: Environment selection
- **WHEN** a caller selects an exact registered environment through `--env` or the existing cwd rules
- **THEN** the command renames only that environment checkout's current branch through the same SDK primitive

#### Scenario: Invalid ticket
- **WHEN** `TICKET` does not match the existing tracker-neutral uppercase ticket-key validation
- **THEN** Click returns an actionable usage error before SDK, catalogue, Git, or network inspection

#### Scenario: Dry-run
- **WHEN** the caller supplies `--dry-run`
- **THEN** Rich/JSON/TOON output presents the complete redacted immutable plan and selected name without renaming the branch, changing upstream configuration, or writing catalogue data

#### Scenario: Machine failure
- **WHEN** detached HEAD, unavailable evidence, changed repository state, or a stale name collision prevents rename
- **THEN** JSON/TOON returns the established bounded failure envelope and non-zero exit status without traceback or partial success output

### Requirement: Rename leaf inventory and documentation

The test-owned `PUBLIC_LEAF_CASES` SHALL contain exactly one `git rename-branch` row with `classification = mutating-or-spawning`, required dry-run coverage, `sdk_primitive = GitResource.rename_branch_command`, and an explicit E2E disposition and rationale. CLI characterization, output-mode, architecture, and README inventory contracts SHALL include the leaf without creating a production registry or duplicate matrix.

#### Scenario: Canonical inventory includes rename
- **WHEN** CLI leaf and SDK-first contract tests enumerate registered commands
- **THEN** `git rename-branch` appears exactly once with its public SDK primitive and dry-run metadata

#### Scenario: README matches executable help
- **WHEN** documentation inventory tests compare README markers with the Click command tree
- **THEN** `git rename-branch TICKET` is documented exactly once with project/environment selector and local-only behavior
