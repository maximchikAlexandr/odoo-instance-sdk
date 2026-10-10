# odoo-git-workflow Specification

## Purpose
TBD - created by archiving change developer-workflow-cli-contracts. Update Purpose after archive.
## Requirements
### Requirement: Frozen Odoo commit context and message

The Git resource SHALL use the already-resolved project ticket settings from the selected project manifest when composing a commit message in an environment worktree. It SHALL NOT re-load `.odcli/project.toml` from the environment worktree root. When `ticket_link_enabled` is `true` and `ticket_base_url` is set in the selected project manifest, the ticket URL SHALL be appended as the second paragraph of the commit message, regardless of whether the worktree has its own `.odcli/project.toml`. Copying `.odcli/project.toml` into each worktree SHALL NOT be required.

#### Scenario: Build a configured ticket commit

- **WHEN** tracker-neutral `ticket_link_enabled` and `ticket_base_url` settings are configured and ticket resolution succeeds by `--ticket`, registered-environment branch, or default-checkout branch precedence
- **THEN** the immutable plan contains `[TAG] module: TICKET description`, a blank line, and configured base URL plus ticket
- **AND** dry-run and execution share scope, tag, ticket, URL, staged paths, and command snapshot
- **AND** no supported identifier, provenance key, machine field, error, or help text names a ticket vendor

#### Scenario: Infer prefix deterministically

- **WHEN** no `--tag` is supplied
- **THEN** prefix precedence is ADD, DEL, PORT, one semantic path bucket, mixed in-module IMP, then outside-module CI or DOC with DOC winning mixed DOC/CI
- **AND** FIX and REF are never inferred from source text

#### Scenario: environment worktree commit includes ticket URL

- **WHEN** `odcli git commit ... --ticket PROJ-123 --dry-run` runs in an environment worktree without its own `.odcli/project.toml` and the selected project has `ticket_link_enabled = true` and `ticket_base_url` set
- **THEN** the commit message contains the ticket in the subject and the URL as the second paragraph

#### Scenario: project root and worktree root differ

- **WHEN** the project root and the worktree root are different paths
- **THEN** the ticket settings come from the selected project manifest and are not reloaded from the worktree root

### Requirement: Commit history validation
`git check [--base REF]` SHALL resolve base by explicit value, recorded environment base, then project default and otherwise fail; it SHALL validate each `BASE..HEAD` commit for configured message, allowed prefix, module boundary, and protected-branch rules, reporting fixup/squash commits as pending and exiting non-zero on any violation. [Source: GH#65]

#### Scenario: Validate feature history
- **WHEN** a feature history includes malformed, cross-module, or pending-fixup commits
- **THEN** bounded equivalent Rich/JSON/TOON diagnostics identify every rejected commit and the command exits non-zero

### Requirement: Optional external absorb adapter
`git absorb` SHALL remain registered regardless of host capability, resolve one absolute `git-absorb` executable before mutation, and delegate staged hunks, the shared base, dry-run, and explicit confirmed `--and-rebase` without reimplementing absorb or auto-staging. Missing capability SHALL produce typed `git_absorb_not_found` with platform install hints and a non-fatal doctor warning. [Source: GH#65]

#### Scenario: Absorb executable appears after installation
- **WHEN** `git-absorb` becomes available on PATH after OdCLI installation
- **THEN** the existing OdCLI installation captures and executes that exact absolute binary without reinstalling or downloading anything

### Requirement: Safe same-branch synchronization
The existing core Git sync plan SHALL continue to perform all Git inspection and mutation through the shared command/action boundary, reject detached/protected branches, dirty state, mismatched upstreams, and unresolved or cross-host origins, fetch without altering unrelated branches, integrate an existing same-name remote branch, and rebase onto the fetched base. SSH origins SHALL retain existing native authentication. `odcli-multica git sync [--base REF] [--push]` SHALL compose that plan with only the root Multica issue creator's host-scoped ephemeral credentials for a configured HTTPS GitLab origin; missing credentials SHALL fail rather than use the machine account. Push SHALL be opt-in, preceded by `git check`, target only `HEAD:refs/heads/<same-name>`, use normal fast-forward publication or an exact fetched-SHA `--force-with-lease`, and never retry a stale lease. Tokens SHALL NOT appear in URLs, argv, Git configuration, public plans, fingerprints, output, or logs. The core wheel SHALL remain usable without Multica.

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

### Requirement: Native Git passthrough with scoped authentication
The public core Git resource SHALL expose a reusable `passthrough_command(args)` operation and delegating convenience method. `odcli-multica git -- <git arguments>` SHALL pass every argument after the delimiter to that native Git boundary without reinterpretation, preserve native stdout, stderr, terminal interaction, signal behavior, and exit code, and coexist with the extension's named `sync` subcommand. For operations that access an allowed HTTPS GitLab remote, the extension SHALL resolve the root creator once and inject a non-interactive host-scoped credential helper only into that child; local-only operations SHALL not require Multica credentials.

#### Scenario: Local native Git command
- **WHEN** a caller runs `odcli-multica git -- status --short`
- **THEN** native Git receives exactly `status --short`, uses the selected checkout cwd, and returns its raw output and exit code without credential resolution

#### Scenario: Remote native Git command
- **WHEN** a caller runs a Git command whose resolved remote is an allowed HTTPS GitLab host
- **THEN** the child receives the root creator's login/token through the private helper boundary and the public argv remains token-free

#### Scenario: Delimiter is absent
- **WHEN** raw Git arguments are supplied without the literal `--`
- **THEN** Click treats them only as existing `odcli-multica git` subcommands/options and never guesses passthrough intent

#### Scenario: Remote host is ambiguous
- **WHEN** arguments and repository remotes identify zero or multiple credential-requiring hosts for a remote operation
- **THEN** the wrapper fails before starting Git rather than sending a credential to an unproven host
