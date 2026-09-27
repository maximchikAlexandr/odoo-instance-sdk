## ADDED Requirements

### Requirement: Backup pinning and retention planning

Public SDK operations SHALL allow setting and clearing pin state for an exact catalog UUID and planning project-scoped retention. The plan SHALL capture UTC cutoff, exact UUIDs/file identities, candidate bytes and protected/skipped IDs with reasons. Eligible candidates SHALL be successful available SDK-managed payloads strictly older than the configured age. Pinning SHALL NOT imply data approval or anonymization.

Pruning SHALL protect pinned backups, active download/validation/restore, references from non-removed environments or unresolved recovery, and the newest available backup per project/source-origin/database group. Equal timestamps SHALL use the existing UUID ordering. Historical restore audit alone SHALL NOT protect all files permanently. Unknown ownership/timestamps, unowned/external payloads and other projects SHALL be skipped. Catalog records/history, databases and environments SHALL never be retention targets.

#### Scenario: Age and latest protection

- **WHEN** a project has several backups older than 14 days for each of two source databases
- **THEN** the plan keeps the newest available backup in each group and selects only unprotected older managed files

#### Scenario: Protected archives

- **WHEN** an old backup is pinned, used by an operation or linked to a non-removed environment or unresolved recovery
- **THEN** it is excluded with its protection reason and byte count

#### Scenario: Preview is inert

- **WHEN** pruning or pinning is previewed
- **THEN** no file, catalog state or audit entry changes

### Requirement: Retention execution rechecks the captured candidates

Prune SHALL reuse existing backup deletion and audit behavior. Before each deletion it SHALL revalidate ownership, file identity and protections under the backup lifecycle lock, without widening the planned set. Busy or changed candidates SHALL be skipped with reasons. A retention policy changed since planning SHALL require replanning; an automatic pass SHALL skip with a warning. Partial failure SHALL report deleted/skipped/failed UUIDs and actual removed bytes accurately; repeat execution SHALL not delete replacement files or lose audit history.

#### Scenario: Restore starts after planning

- **WHEN** a planned candidate becomes active in restore before pruning acquires its lifecycle lock
- **THEN** pruning skips it and does not disturb restore

#### Scenario: A candidate is pinned or replaced

- **WHEN** pin state or file identity changes after planning
- **THEN** execution does not delete that candidate

#### Scenario: Partial filesystem failure

- **WHEN** one candidate cannot be removed after another was deleted
- **THEN** the result reports both outcomes and catalog state reflects only completed deletions

### Requirement: Opportunistic cleanup follows primary success

When user-level auto-prune is enabled, project-aware backup creation, refresh, local restore and checkout SHALL include at most one pruning pass after primary success, within the same process. The outermost immutable plan SHALL capture existing candidates before primary mutation; execution SHALL exclude that operation's input/output backup UUIDs and recheck protections. Nested operations SHALL NOT schedule additional passes. Failure, cancellation, read-only inspection and dry-run SHALL NOT trigger pruning.

#### Scenario: Successful operation performs maintenance

- **WHEN** auto-prune is enabled and a project-aware backup/restore operation succeeds
- **THEN** its planned eligible old files are processed once after primary success

#### Scenario: Primary operation fails

- **WHEN** download, restore, neutralization or checkout fails or is cancelled
- **THEN** no automatic pruning runs and recovery artifacts remain available

#### Scenario: Maintenance fails after success

- **WHEN** restore succeeds but its pruning pass fails
- **THEN** primary success is preserved with a structured maintenance warning so the caller does not repeat restore

## MODIFIED Requirements

### Requirement: Удаление backup

`client.backups.delete(backup)` MUST verify catalog identity and delete the file when present, except that pinned backups and backups actively consumed by validation/restore SHALL be refused before deletion under the lifecycle lock. Unpinning SHALL require an explicit separate operation.

In one transaction it MUST set state `deleted`, set `deleted_at` and append a `deleted` event after successful file removal. Catalog rows and audit events MUST remain. Filesystem failure MUST NOT claim successful deletion.

Deletion MUST be idempotent: repeat calls return `already_deleted=True` without filesystem errors; an initially missing file returns `file_existed=False`.

#### Scenario: Удаление существующего файла

- **WHEN** an available, unpinned and unused backup file exists
- **THEN** deletion removes it, marks its row deleted and appends an audit event

#### Scenario: Повторное удаление

- **WHEN** deletion is called for an already deleted backup
- **THEN** it returns an idempotent result without another filesystem error

#### Scenario: Pin or active consumer blocks deletion

- **WHEN** the backup is pinned or actively used
- **THEN** direct deletion and pruning both leave its file intact
