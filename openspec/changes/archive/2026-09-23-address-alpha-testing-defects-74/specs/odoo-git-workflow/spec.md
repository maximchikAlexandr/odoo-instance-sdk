## MODIFIED Requirements

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
