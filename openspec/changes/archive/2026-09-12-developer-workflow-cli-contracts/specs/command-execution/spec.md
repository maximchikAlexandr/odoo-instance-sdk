## ADDED Requirements

### Requirement: Nested process plans remain visible
Every Rich dry-run SHALL render each nested `ProcessStep.display` in execution order whenever the machine plan contains it, including command-specific module summaries; renderers SHALL use the existing sanitized immutable projection and SHALL NOT rebuild argv or expose stdin/secrets. [Source: GH#64 §6]

#### Scenario: Module update preview contains process steps
- **WHEN** `module update MODULE --dry-run` produces Compose probes and an Odoo shell process in its machine plan
- **THEN** Rich includes the same ordered sanitized process commands and executes no process/action step

### Requirement: Git operations use the shared execution boundary
All Git workflow probes and mutations SHALL be captured as immutable process/action steps, revalidated before mutation, and launched only through `internal/proc`; dry-run SHALL perform no fetch, rebase, commit, absorb, or push. [Source: GH#65]

#### Scenario: Inspect Git plan
- **WHEN** any mutating `odcli git` command is requested with `--dry-run`
- **THEN** output contains every sanitized exact command and precondition while repository and remotes remain unchanged
