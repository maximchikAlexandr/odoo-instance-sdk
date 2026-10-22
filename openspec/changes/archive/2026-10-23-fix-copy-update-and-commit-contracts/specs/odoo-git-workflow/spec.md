## MODIFIED Requirements

### Requirement: Frozen Odoo commit context and message

The Git resource SHALL use the already-resolved project ticket settings from the selected project manifest when composing a commit message in an environment worktree. It SHALL NOT re-load `.odcli/project.toml` from the environment worktree root. When `ticket_link_enabled` is `true` and `ticket_base_url` is set in the selected project manifest, the generated ticket URL SHALL be appended as a bare URL in the second paragraph of the commit message, regardless of whether the worktree has its own `.odcli/project.toml`. Copying `.odcli/project.toml` into each worktree SHALL NOT be required. History validation SHALL accept both existing angle-bracketed and bare URL paragraphs plus optional standard `Co-authored-by` trailers without accepting arbitrary extra paragraphs.

Explicit commit prefixes SHALL support `ADD`, `DEL`, `PORT`, `I18N`, `UI`, `VIEW`, `DEMO`, `DATA`, `ACC`, `TEST`, `DOC`, `CI`, `IMP`, `FIX`, and `REF` in both message generation and history validation. Adding explicit prefixes SHALL NOT change deterministic prefix inference.

Project-aware commit SHALL accept a selected verified member UUID without requiring explicit repository mode. It SHALL retain the selected initialized project/environment worktree, module boundary, ticket, prefix, and message policy; resolve the repository host; verify that member through the existing host-scoped GitLab credential contract; and capture the verified name and email in `GIT_AUTHOR_*` and `GIT_COMMITTER_*` for the same immutable commit step. It SHALL NOT use repository/global Git identity, a GitLab numeric ID, an agent or assignee, or a machine account as fallback. Public preview SHALL expose only safe verified name/email fields and SHALL NOT expose credentials.

#### Scenario: Use an explicit policy prefix

- **WHEN** a caller supplies `VIEW`, `DEMO`, `DATA`, or `ACC` to commit generation
- **THEN** preview and execution retain that prefix and the resulting correctly scoped commit passes history validation
- **AND** existing `UI` commits remain supported

#### Scenario: Build a configured ticket commit

- **WHEN** tracker-neutral `ticket_link_enabled` and `ticket_base_url` settings are configured and ticket resolution succeeds by `--ticket`, registered-environment branch, or default-checkout branch precedence
- **THEN** the immutable plan contains `[TAG] module: TICKET description`, a blank line, and the configured base URL plus ticket as a bare URL
- **AND** dry-run and execution share scope, tag, ticket, URL, staged paths, identity environment, and command snapshot
- **AND** no supported identifier, provenance key, machine field, error, or help text names a ticket vendor

#### Scenario: Commit link preserves tracker path

- **WHEN** `ticket_base_url` is configured as `https://tracker.example/issues/` and the resolved ticket is `PROJ-123`
- **THEN** the commit message contains `https://tracker.example/issues/PROJ-123` as its bare second paragraph

#### Scenario: Git trailer hook preserves the ticket link

- **WHEN** a `prepare-commit-msg` hook appends a standard `Co-authored-by` trailer with `git interpret-trailers`
- **THEN** the committed second paragraph keeps the exact bare ticket URL and the trailing co-author is accepted by history validation

#### Scenario: Existing angle-bracketed history remains valid

- **WHEN** history validation reads a pre-change commit whose second paragraph is `<https://tracker.example/issues/PROJ-123>`
- **THEN** it accepts that link form under the existing message and trailer rules

#### Scenario: Infer prefix deterministically

- **WHEN** no `--tag` is supplied
- **THEN** prefix precedence is ADD, DEL, PORT, one semantic path bucket, mixed in-module IMP, then outside-module CI or DOC with DOC winning mixed DOC/CI
- **AND** FIX and REF are never inferred from source text

#### Scenario: environment worktree commit includes ticket URL

- **WHEN** `odcli git commit ... --ticket PROJ-123 --dry-run` runs in an environment worktree without its own `.odcli/project.toml` and the selected project has `ticket_link_enabled = true` and `ticket_base_url` set
- **THEN** the commit message contains the ticket in the subject and the bare URL as the second paragraph

#### Scenario: project root and worktree root differ

- **WHEN** the project root and the worktree root are different paths
- **THEN** the ticket settings come from the selected project manifest and are not reloaded from the worktree root

#### Scenario: Project-aware commit uses verified member identity

- **WHEN** an initialized project or registered environment commit receives a mapped member UUID whose GitLab token owner has usable commit name and email
- **THEN** dry-run shows that safe name/email, execution uses them as both author and committer on the exact captured commit step, and Odoo message and module policy remain active

#### Scenario: Unverified member fails before commit

- **WHEN** the member mapping, token owner, repository host, commit name, or email cannot be verified
- **THEN** commit planning fails before `git commit` and does not fall back to ambient or machine Git identity
