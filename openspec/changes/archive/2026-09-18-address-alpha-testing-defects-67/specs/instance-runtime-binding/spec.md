## ADDED Requirements

### Requirement: Canonical worktree path is the single source

All execution plans, runtime/session ownership, provenance, and configuration SHALL resolve environment worktree paths from the canonical `~/.odcli` root. The legacy `~/Library/Application Support/odoo-instance-sdk` path SHALL be used only by the one-time migration/compatibility code. After migration, no production read, write, command construction, runtime identity, cleanup, or documentation SHALL depend on the legacy path or its compatibility symlink. The repository and tests SHALL verify the legacy path is not used outside the bounded migration/compatibility code.

#### Scenario: Run uses canonical worktree path

- **WHEN** `odcli --project /path --env UUID run --dry-run` builds a plan
- **THEN** `cwd`, `--addons-path`, and Git provenance commands reference the canonical `~/.odcli` worktree path

#### Scenario: Run works without legacy symlink

- **WHEN** the compatibility symlink and legacy directory are removed
- **THEN** `run` and `run --dry-run` still work for a registered environment

#### Scenario: Runtime ownership is consistent across commands

- **WHEN** a registered environment's Odoo is running
- **THEN** `env list`, `run --dry-run`, port preflight, and active-session lookup all recognize the same runtime and owner