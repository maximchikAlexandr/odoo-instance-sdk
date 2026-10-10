## ADDED Requirements

### Requirement: Successful preparation atomically binds the main checkout
When preparation targets a selected main checkout, refresh and restore SHALL use the existing common source, validation, restore, neutralization, audit, and postcondition pipeline. After all requested postconditions succeed, one atomic project-binding update SHALL record the target database, exact restore/backup identity, managed filestore root, and configuration used by subsequent project `run`. Download-only refresh SHALL return its backup identity without changing the runtime binding. Environment preparation SHALL retain its existing environment binding behavior.

#### Scenario: Restore binds database and filestore
- **WHEN** `db refresh --restore --project PATH` or `db restore --project PATH` completes every restore postcondition
- **THEN** the result contains the backup identity, restored database, effective configuration, managed filestore root, and project identity
- **AND** the next project `run` uses that exact database and filestore

#### Scenario: Download only preserves binding
- **WHEN** project refresh downloads a backup without restore
- **THEN** it returns the retained backup identity and leaves the prior database/filestore binding unchanged

### Requirement: Failed preparation preserves the prior main-checkout binding
Planning SHALL capture the current project binding and execution SHALL compare it before publication. Any source, validation, restore, neutralization, reset, postcondition, filestore, stale-plan, or binding-publication failure SHALL retain the previous active project binding while preserving existing retained-artifact evidence. A concurrently changed binding SHALL fail stale rather than overwrite the newer state.

#### Scenario: Failure after database creation
- **WHEN** a new database is confirmed but filestore binding or final project publication fails
- **THEN** the prior binding remains active and the typed failure identifies the retained backup/database plus the failed stage without secrets

#### Scenario: Binding changes during preparation
- **WHEN** another successful operation changes the project binding after planning
- **THEN** the stale operation refuses final publication and does not restore its captured old value over the newer binding
