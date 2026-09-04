## ADDED Requirements

### Requirement: Guarded project-cluster database deletion

Database deletion SHALL be planned and executed by a CLI-private/internal operation only through the resolved project's bound PostgreSQL cluster and existing PostgreSQL transport. It SHALL NOT add a public `DatabaseResource` or `PostgresCluster` method and SHALL preserve the existing public `DatabaseResource.drop/drop_command` Odoo HTTP manager semantics and call-time master-password requirement. The operation SHALL accept one exact normalized database name, reject empty/wildcard names and the exact denylist `postgres`, `template0`, and `template1`, query `pg_database.datistemplate` read-only for the exact target, and reject every database for which it is true. It SHALL display cluster identity without credentials and verify the target exists. It SHALL refuse the configured project default unless explicitly forced. It SHALL report active connection count and identities in sanitized form and SHALL refuse to terminate them unless explicitly forced; forced termination and `DROP DATABASE` SHALL be separate inspectable steps. It SHALL connect to the exact maintenance database `postgres`; because `postgres` is denied as a target, the target and maintenance database cannot coincide. Immediately before any session termination or drop, execution SHALL revalidate existence, exact denylist, `datistemplate`, configured-default, and active-session preconditions and SHALL fail closed without mutation if any safety value changed or cannot be read. Passwords SHALL never appear in argv, plans, errors, or logs.

#### Scenario: Target cannot escape the cluster
- **WHEN** deletion is requested for a database name on a resolved project
- **THEN** all inspection, termination, and drop actions use that project's PostgreSQL transport and no caller-supplied host/user/password is accepted

#### Scenario: Active connections require force
- **WHEN** the target has active sessions and connection force is absent
- **THEN** the operation reports the sessions and performs neither termination nor drop

#### Scenario: Custom template database is refused
- **WHEN** the exact target is not in the name denylist but `pg_database.datistemplate` is true during planning or execution revalidation
- **THEN** the operation fails closed and performs no session termination, drop, or catalogue write

#### Scenario: Forced drop is ordered
- **WHEN** active sessions exist and all required confirmations and force flags are present
- **THEN** the command terminates only target-database sessions, drops that exact database, and verifies absence

#### Scenario: Public SDK drop remains unchanged
- **WHEN** public SDK methods and their behavior are characterized after the CLI drop is added
- **THEN** `DatabaseResource.drop/drop_command` still use the Odoo HTTP database manager with a call-time master password and `test_discovered_public_methods` reports the unchanged public method set

### Requirement: Drop reconciles the audit catalogue

After verified successful deletion, the operation SHALL invoke the existing canonical `record_database_dropped` reconciliation helper exactly once for the bound cluster key and database. The helper SHALL preserve its existing idempotency rule: it inserts a sanitized `dropped` event only when the latest event is not already `dropped`; otherwise it performs its canonical no-op. A failed, refused, or dry-run deletion SHALL not invoke successful reconciliation and SHALL write no catalogue event or mapping change.

#### Scenario: Successful deletion is audited
- **WHEN** the database is absent after the drop postcondition
- **THEN** `record_database_dropped` is called exactly once and inserts a new `dropped` row only when the latest event for that cluster/database is not already `dropped`

#### Scenario: Existing dropped event remains idempotent
- **WHEN** the database was recreated outside the catalogue, successfully dropped by the CLI, and its latest catalogue event is already `dropped`
- **THEN** reconciliation is invoked once and the canonical helper inserts no duplicate event

#### Scenario: Failure leaves catalogue unchanged
- **WHEN** termination, drop, or the absence postcondition fails
- **THEN** no successful dropped event or mapping reconciliation is committed
