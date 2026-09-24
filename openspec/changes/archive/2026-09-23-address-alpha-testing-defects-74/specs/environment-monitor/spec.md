## MODIFIED Requirements

### Requirement: Component failure isolation

A failed, empty, or unparseable Docker resource/metrics snapshot SHALL NOT turn a running cluster into `STOPPED`. `stopped` SHALL follow only from a successful `PostgresCluster.status_command()`. A metrics failure (`stats_failed`) SHALL degrade only metrics and SHALL keep the actual lifecycle state. One output SHALL NOT simultaneously carry a false lifecycle state and a diagnostic metrics error for the same snapshot.

#### Scenario: One environment failure isolated

- **WHEN** Git CLI fails for one environment's worktree
- **THEN** that environment's `git.state == "orphan"`, `ahead`/`behind`/`diff`/`head_sha` are None, `branch == "unknown"`; other environments are unaffected

#### Scenario: Catalog error fails snapshot

- **WHEN** `BackupCatalog` raises `BackupCatalogError` during `list_environments()`
- **THEN** `snapshot()` raises typed `MonitorError`, not a generic `sqlite3.Error`

#### Scenario: Missing psutil extra actionable hint

- **WHEN** an installation is manually corrupted by removing required core `psutil`
- **THEN** that unsupported installation has no `metrics` extra or `MonitorExtrasMissingError` compatibility contract

#### Scenario: metrics failure does not change lifecycle state

- **WHEN** a running cluster's Docker metrics snapshot is empty, unparseable, or failed
- **THEN** the lifecycle state comes from `PostgresCluster.status_command()` and `stats_failed` degrades only metrics

#### Scenario: no simultaneous false state and metrics error

- **WHEN** a monitor snapshot is produced for a running cluster with failed metrics
- **THEN** the output does not carry `stopped` alongside `stats_failed` for the same snapshot
