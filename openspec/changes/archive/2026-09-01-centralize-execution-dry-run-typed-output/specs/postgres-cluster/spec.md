## ADDED Requirements

### Requirement: Inspectable PostgreSQL process operations

Every public `PostgresCluster` operation that can launch Docker, Compose, psql, pg_restore, or another child process SHALL expose a command sibling and SHALL execute captured steps through `internal/proc`. This includes read-only process operations such as image resolution/status when they spawn, even when the CLI does not expose dry-run for the corresponding read-only leaf.

#### Scenario: Compose startup is inspected

- **WHEN** an owned Compose cluster `ensure_running_command()` is built
- **THEN** the plan shows redacted image/config validation, Compose up, and health/status process steps known before artifact publication or startup
- **AND** private password material is absent from the projection

#### Scenario: External cluster operation

- **WHEN** an operation targets an externally owned cluster
- **THEN** existing ownership and no-op/error semantics remain unchanged
- **AND** no unplanned Compose command is introduced

### Requirement: PostgreSQL runner consolidation

Compose and PostgreSQL transport protocols that exist only to wrap subprocess SHALL be replaced by the shared executor or thin domain functions over it. Secret-file, image trust, timeout, serialized lifecycle, and error-typing behavior SHALL remain intact.

#### Scenario: Existing PostgreSQL tests migrate

- **WHEN** PostgreSQL unit tests record command execution
- **THEN** they inject the shared fake/recording executor instead of monkeypatching module-local subprocess or using a second runner interface
