## ADDED Requirements

### Requirement: Project removal reuses one authoritative resource projection

The existing local resource inventory SHALL provide the project-removal planner with a deterministic projection covering project/environment runtimes and publications, environments, worktrees, Python environments, configs, secrets, databases, filestores, backups, logs, temporary artifacts, Compose containers/volumes, and the registered source root. Each relationship SHALL be scoped to the selected canonical `project_id`, and each resource SHALL have exactly one ownership/exclusive-use decision without adding a second catalogue.

#### Scenario: Complete project projection

- **WHEN** a registered project has resources across the catalogue, filesystem, PostgreSQL, Docker, runtime, and publication providers
- **THEN** one read-only projection relates every known resource to the full project id and records whether it is deletable, retained, active, incomplete, or unknown

#### Scenario: Shared identity is deduplicated

- **WHEN** multiple environments reference one shared Python environment, backup, database, volume, or checkout
- **THEN** the projection emits one stable resource identity with all relationships and does not classify it as exclusively deletable

#### Scenario: Planning does not repair evidence

- **WHEN** inventory finds a missing path, stale row, unknown file, legacy record, or mismatched label
- **THEN** it reports the observation and does not reconcile, adopt, delete, relabel, or mutate it

### Requirement: Purge ownership decisions are fail-closed

A purge delete decision SHALL require both proven SDK/project ownership and exclusive use at plan and execution time. Containment SHALL use canonical no-symlink paths. A project source root SHALL additionally match registered Git provenance and SHALL NOT be a linked, caller-owned, or other-tool-managed checkout. Unknown or conflicting evidence SHALL produce a retained decision with a stable reason, never a forceable delete action.

#### Scenario: Symlink escape is retained

- **WHEN** an owned directory contains a symlink or canonical target outside its allowed root
- **THEN** purge retains the escaping entry and never traverses or deletes its target

#### Scenario: Borrowed or referenced backup is retained

- **WHEN** a backup is borrowed by an environment, belongs to another project, or still has an active reference
- **THEN** purge retains it and reports every blocking relationship

#### Scenario: Exact owned Compose volume is deletable

- **WHEN** the active cluster claim, project id, compose project, container label, volume label, database state, and exclusive-use checks all match
- **THEN** the projection may place container stop/removal and volume deletion after dependent database/environment cleanup
