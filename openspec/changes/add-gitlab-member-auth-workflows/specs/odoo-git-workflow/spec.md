## ADDED Requirements

### Requirement: Explicit repository mode preserves Odoo policy boundaries

The existing core Git commands SHALL accept explicit `--repo` and `--member-id` where specified by the generic GitLab workflow without requiring root `--project` or `--env`. Repository resolution SHALL decide once whether the root is an initialized OdCLI project/worktree. Odoo-specific message, module-boundary, ticket-link, and `git check` rules SHALL apply only in that initialized context; generic repositories SHALL retain shared branch, staged-state, remote, and publication safety without Odoo formatting.

#### Scenario: Foreign repository is not treated as Odoo
- **WHEN** `--repo` names a valid GitLab repository with no resolved OdCLI project manifest
- **THEN** commit and sync run through generic policy and do not require an Odoo module, generated config, database, or catalog registration

#### Scenario: Odoo root retains existing validation
- **WHEN** `--repo` names an initialized Odoo project or registered worktree
- **THEN** the existing Odoo commit/history requirements remain unchanged in addition to selected-member authentication
