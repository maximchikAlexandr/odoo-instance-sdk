## MODIFIED Requirements

### Requirement: Safe same-branch synchronization
`git sync [--base REF] [--push]` SHALL plan all Git inspection and mutation through the shared command/action boundary, reject detached/protected branches, dirty state, mismatched upstreams, and unresolved or cross-host origins, fetch without altering unrelated branches, integrate an existing same-name remote branch, and rebase onto the fetched base. SSH origins SHALL retain existing native authentication. A configured HTTPS GitLab origin SHALL use only the root Multica issue creator's host-scoped ephemeral credentials from `multica-user-credentials`; missing credentials SHALL fail rather than use the machine account. Push SHALL be opt-in, preceded by `git check`, target only `HEAD:refs/heads/<same-name>`, use normal fast-forward publication or an exact fetched-SHA `--force-with-lease`, and never retry a stale lease. Tokens SHALL NOT appear in URLs, argv, Git configuration, public plans, fingerprints, output, or logs.

#### Scenario: Publish rebased feature branch
- **WHEN** a previously published clean feature branch is rebased and `--push` is confirmed
- **THEN** publication uses `--force-with-lease=refs/heads/<branch>:<fetched-sha>` after validation
- **AND** a post-fetch remote change fails without refetch/retry

#### Scenario: Rebase conflicts
- **WHEN** synchronization reaches a rebase conflict
- **THEN** Git's ordinary conflict state remains and diagnostics give exact `git rebase --continue` and `git rebase --abort` guidance

#### Scenario: HTTPS sync uses the root creator
- **WHEN** origin is an allowed GitLab HTTPS URL and the root issue creator has one matching credential mapping
- **THEN** remote SHA inspection, fetch, and push use that credential only in their child-process environment
- **AND** repository/global credential settings and the origin URL remain unchanged

#### Scenario: HTTPS credentials are missing
- **WHEN** origin is HTTPS but root-creator or host credentials cannot be resolved
- **THEN** synchronization fails before `ls-remote`, fetch, rebase, or push and does not try ambient credentials

## ADDED Requirements

### Requirement: Native Git passthrough with scoped authentication
The public Git resource SHALL expose a `passthrough_command(args)` operation and delegating convenience method. `odcli git -- <git arguments>` SHALL pass every argument after the delimiter to native Git without reinterpretation, preserve native stdout, stderr, terminal interaction, signal behavior, and exit code, and coexist with existing `git commit`, `check`, `absorb`, and `sync` subcommands. For operations that access an allowed HTTPS GitLab remote, it SHALL resolve the root creator once and inject a non-interactive host-scoped credential helper only into that child; local-only operations SHALL not require Multica credentials.

#### Scenario: Local native Git command
- **WHEN** a caller runs `odcli git -- status --short`
- **THEN** native Git receives exactly `status --short`, uses the selected checkout cwd, and returns its raw output and exit code without credential resolution

#### Scenario: Remote native Git command
- **WHEN** a caller runs a Git command whose resolved remote is an allowed HTTPS GitLab host
- **THEN** the child receives the root creator's login/token through the private helper boundary and the public argv remains token-free

#### Scenario: Delimiter is absent
- **WHEN** raw Git arguments are supplied without the literal `--`
- **THEN** Click treats them only as existing OdCLI Git subcommands/options and never guesses passthrough intent

#### Scenario: Remote host is ambiguous
- **WHEN** arguments and repository remotes identify zero or multiple credential-requiring hosts for a remote operation
- **THEN** the wrapper fails before starting Git rather than sending a credential to an unproven host
