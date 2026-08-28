## ADDED Requirements

### Requirement: Executable environment commands

Every `EnvironmentResource` operation that can launch a child process SHALL expose a sibling command method, including checkout, Python synchronization, refresh/database preparation, removal, and any process-spawning pgAdmin path found by the migration audit. Existing convenience methods SHALL delegate to those commands.

#### Scenario: Checkout command is inspected and run

- **WHEN** a caller builds `checkout_command(project, branch, options=...)`, inspects it, and calls `.run()`
- **THEN** the exact captured Git, uv, Odoo, PostgreSQL, and action steps shown by the command are consumed in order

### Requirement: Checkout domain-plan compatibility

`plan_checkout()` SHALL remain the public domain projection and `checkout_with_plan()` SHALL remain compatible. Both SHALL derive from the same captured checkout command used by `checkout()`; no method SHALL rebuild executable steps independently.

#### Scenario: Existing checkout plan caller

- **WHEN** an existing caller uses `plan_checkout()` or `checkout_with_plan()`
- **THEN** it receives the documented `EnvironmentCheckoutPlan` fields
- **AND** subsequent execution uses the captured command snapshot corresponding to that plan

### Requirement: Checkout planning and stale safety

Checkout command construction SHALL create no environment root, worktree, venv, config, lock, catalog migration, database, backup, or runtime record. Execution SHALL revalidate the captured base revision, paths, port, database provenance, and identity under the existing operation locks and SHALL fail stale before mutation rather than recompute them.

#### Scenario: Checkout preview remains non-mutating

- **WHEN** checkout command construction or CLI dry-run runs against an empty target
- **THEN** every target artifact remains absent and all required read-only process probes are recorded as observations

#### Scenario: Allocated port becomes occupied

- **WHEN** the captured checkout port becomes unavailable before `.run()`
- **THEN** checkout raises `StalePlanError` before creating catalog or filesystem state
- **AND** it does not allocate a replacement port silently
