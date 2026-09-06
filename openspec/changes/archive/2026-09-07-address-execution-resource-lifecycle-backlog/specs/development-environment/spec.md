## ADDED Requirements

### Requirement: Environment mutation progress

Normal Rich execution of `env checkout` and `env sync` SHALL observe the existing immutable command plan and report each genuinely long logical step as started, completed, or failed with elapsed time. Non-TTY Rich output SHALL use sparse deterministic lines; TTY Rich MAY update a live status. Dry-run SHALL show only the plan, and JSON/TOON SHALL emit no progress.

#### Scenario: Slow checkout step is visible

- **WHEN** a controlled checkout step remains running after it starts
- **THEN** Rich output identifies that step before it finishes and later reports its completion or failure with elapsed time

#### Scenario: Checkout dry-run

- **WHEN** `env checkout --dry-run` is rendered in any format
- **THEN** it reports planned steps but no step is presented as started or completed

#### Scenario: Environment machine output

- **WHEN** checkout or sync runs in JSON or TOON mode
- **THEN** stdout is exactly one final envelope without progress or ANSI content
