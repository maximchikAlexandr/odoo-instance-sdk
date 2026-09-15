## ADDED Requirements

### Requirement: CheckoutInventory with main checkout row

`EnvironmentResource` SHALL expose a `CheckoutInventory` projection that includes the main checkout of each selected project as the first typed row with `kind = main | environment`, a stable `project_id`, and a nullable `environment_id`. The main checkout SHALL NOT be modelled as a synthetic environment. The base row SHALL contain working identity and state: kind/name and project; branch, short SHA, and canonical worktree path; commits ahead of the base branch plus added and deleted lines; compact Odoo status `running | stopped | unavailable` without PID or metrics; and bound database/DB mode when applicable.

`CheckoutInventory` SHALL be built from one canonical `EnvironmentMonitor.snapshot()` per sample plus Git facts of the main checkout. A separate monitor or collector SHALL NOT be added. The raw `EnvironmentMonitor.snapshot()` SHALL remain the canonical source for `odcli ps`, the Python SDK, FastAPI, and the dashboard.

#### Scenario: Main checkout is the first row

- **WHEN** `CheckoutInventory` is built for a project with one environment
- **THEN** the first row has `kind=main` and no synthetic environment is created

#### Scenario: Stopped checkout stays visible

- **WHEN** the main checkout's Odoo is stopped
- **THEN** the main checkout row remains visible with status `stopped` and no PID

### Requirement: Multi-target environment removal

`EnvironmentResource` removal SHALL accept multiple full UUIDs/selectors. For each explicit target, the persisted repository, Git common dir, worktree, and PostgreSQL cluster SHALL be resolved independently; the project/cluster of the current cwd SHALL NOT be applied to the whole set. A call without a positional argument SHALL preserve the existing cwd semantics for exactly one environment.

Shared environment cleanup SHALL NOT drop the database. Copy environment cleanup SHALL preserve the full guarded direct PostgreSQL drop, backup, config, venv, and worktree cleanup contract. Locks and fail-closed checks for dirty worktree, active process/occupied port, unsafe paths, copy journal, cluster/restore ownership, and retained cleanup state SHALL be preserved. Execution SHALL be sequential in argument order with per-target execution-time revalidation. A per-target failure SHALL continue remaining prepared targets and return per-target success/failure.

#### Scenario: Multiple UUIDs resolved independently

- **WHEN** `env rm UUID1 UUID2` runs with UUIDs from different projects
- **THEN** each target's repository and cluster context is resolved separately

#### Scenario: No arguments preserves cwd semantics

- **WHEN** `env rm` runs from inside an exact registered worktree
- **THEN** it resolves exactly that environment

#### Scenario: Shared environment does not drop database

- **WHEN** a shared environment is removed
- **THEN** its database is not dropped

#### Scenario: Copy environment full cleanup

- **WHEN** a copy environment is removed
- **THEN** the guarded PostgreSQL drop, backup, config, venv, and worktree cleanup all run