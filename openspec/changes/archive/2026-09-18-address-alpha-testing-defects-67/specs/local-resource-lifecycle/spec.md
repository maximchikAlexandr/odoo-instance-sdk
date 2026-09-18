## ADDED Requirements

### Requirement: Legacy path is not used outside migration compatibility

After the one-time storage migration, the legacy `~/Library/Application Support/odoo-instance-sdk` path SHALL NOT be used for reads, writes, command construction, runtime identity, cleanup, or documentation outside the explicitly bounded migration/compatibility code. Execution plans, runtime/session ownership, provenance, and configuration SHALL resolve environment worktree paths from the canonical `~/.odcli` root. The repository and tests SHALL verify the legacy path is not used outside the bounded migration/compatibility code.

#### Scenario: Run plan uses canonical path

- **WHEN** `odcli --env UUID run --dry-run` builds a plan after migration
- **THEN** `cwd`, `--addons-path`, and Git provenance commands reference the canonical `~/.odcli` worktree path

#### Scenario: Legacy path absent outside migration code

- **WHEN** the repository is searched for the legacy path outside migration/compatibility modules
- **THEN** no production read, write, command construction, runtime identity, cleanup, or documentation references it