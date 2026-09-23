## MODIFIED Requirements

### Requirement: Frozen Odoo commit context and message

The Git resource SHALL use the already-resolved project ticket settings from the selected project manifest when composing a commit message in an environment worktree. It SHALL NOT re-load `.odcli/project.toml` from the environment worktree root. When `ticket_link_enabled` is `true` and `ticket_base_url` is set in the selected project manifest, the ticket URL SHALL be appended as the second paragraph of the commit message, regardless of whether the worktree has its own `.odcli/project.toml`. Copying `.odcli/project.toml` into each worktree SHALL NOT be required.

#### Scenario: environment worktree commit includes ticket URL

- **WHEN** `odcli git commit ... --ticket PROJ-123 --dry-run` runs in an environment worktree without its own `.odcli/project.toml` and the selected project has `ticket_link_enabled = true` and `ticket_base_url` set
- **THEN** the commit message contains the ticket in the subject and the URL as the second paragraph

#### Scenario: project root and worktree root differ

- **WHEN** the project root and the worktree root are different paths
- **THEN** the ticket settings come from the selected project manifest and are not reloaded from the worktree root