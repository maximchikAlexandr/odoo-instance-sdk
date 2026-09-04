## ADDED Requirements

### Requirement: Guarded project-cluster database deletion

Database deletion SHALL be planned and executed only through the resolved project's bound PostgreSQL cluster. The operation SHALL accept one exact normalized database name, reject empty/wildcard names and PostgreSQL template/system databases, display cluster identity without credentials, and verify the target exists. It SHALL refuse the configured project default unless explicitly forced. It SHALL report active connection count and identities in sanitized form and SHALL refuse to terminate them unless explicitly forced; forced termination and `DROP DATABASE` SHALL be separate inspectable steps. Passwords SHALL never appear in argv, plans, errors, or logs.

#### Scenario: Target cannot escape the cluster
- **WHEN** deletion is requested for a database name on a resolved project
- **THEN** all inspection, termination, and drop actions use that project's PostgreSQL transport and no caller-supplied host/user/password is accepted

#### Scenario: Active connections require force
- **WHEN** the target has active sessions and connection force is absent
- **THEN** the operation reports the sessions and performs neither termination nor drop

#### Scenario: Forced drop is ordered
- **WHEN** active sessions exist and all required confirmations and force flags are present
- **THEN** the command terminates only target-database sessions, drops that exact database, and verifies absence

### Requirement: Drop reconciles the audit catalogue

After verified successful deletion, the backup/restore catalogue SHALL record a sanitized database `dropped` event and reconcile any current database mapping for the same cluster key. A failed, refused, or dry-run deletion SHALL write no catalogue event or mapping change.

#### Scenario: Successful deletion is audited
- **WHEN** the database is absent after the drop postcondition
- **THEN** exactly one dropped event is recorded for the bound cluster and database

#### Scenario: Failure leaves catalogue unchanged
- **WHEN** termination, drop, or the absence postcondition fails
- **THEN** no successful dropped event or mapping reconciliation is committed
