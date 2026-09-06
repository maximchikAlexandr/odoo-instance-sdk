## ADDED Requirements

### Requirement: Read-only local resource inventory

The CLI-private/internal implementation behind `odcli resource list` SHALL provide one deterministic read-only projection of known local backups, project-cluster databases, proven or possible filestores, owned and shared environment worktrees/Python environments, SDK logs, and SDK-owned Compose volumes by composing existing public SDK methods, catalogue queries, and collectors. It SHALL add no public SDK resource method. Each resource SHALL expose stable identity, type, relationships, ownership confidence, active-use state, measured bytes and/or logical bytes with their meaning, completeness, and reclaimability. The projection SHALL NOT create a second resource catalogue or mutate lifecycle state.

#### Scenario: Related restore resources

- **WHEN** an available backup has been restored into two databases
- **THEN** inventory shows one backup related to both exact cluster/database identities and does not imply that deleting one deletes either of the others

#### Scenario: Logical database size

- **WHEN** PostgreSQL reports a database logical size
- **THEN** inventory labels it as logical database bytes and does not present it as guaranteed host disk reclamation

#### Scenario: Default checkout has resources

- **WHEN** an initialized project has no registered environment but has a project runtime, database, logs, or owned cluster
- **THEN** those project-context resources appear without creating a synthetic environment

#### Scenario: Inventory is read-only

- **WHEN** inventory observes missing or unknown resources
- **THEN** it creates, deletes, repairs, and reclassifies nothing

#### Scenario: Resource projection does not expand the public SDK

- **WHEN** resource projection is added and `test_discovered_public_methods` characterizes the SDK
- **THEN** the canonical public method set remains unchanged and projection stays CLI-private/internal

### Requirement: Local resource doctor

`odcli resource doctor` SHALL diagnose available-catalogue backups with missing files, crash-left `.part` files under SDK-owned backup directories, unknown files under SDK-owned directories, preserved unknown filestores, cleanup-failed environments, and unavailable measurements. It SHALL sanitize paths and diagnostics, distinguish handled download cleanup from crash leftovers, perform no automatic deletion, and recommend only the applicable existing point command.

#### Scenario: Crash-left partial backup

- **WHEN** an unreferenced `.part` exists under the owned backup directory after abnormal termination
- **THEN** doctor reports it as a possible crash leftover with measured bytes and does not delete it

#### Scenario: Handled failure has no part

- **WHEN** a failed catalogue download was already cleaned by the normal error path
- **THEN** doctor reports no fabricated leftover file for that record

#### Scenario: Cleanup-failed environment

- **WHEN** an environment remains in `cleanup_failed`
- **THEN** doctor reports its retained owned artifacts and recommends the existing `env remove` retry without deleting source databases, Git branches, or shared Python environments

### Requirement: Resource policy automation remains gated

Age-based backup pruning, automatic log rotation, and complete PostgreSQL cluster destruction SHALL NOT be added by this change. Any future implementation SHALL require a separate previewable change whose policy uses the point-delete and ownership contracts, protects active references, distinguishes `stop` from destroy, and has measured evidence that the targeted storage is material.

#### Scenario: Old backup is listed

- **WHEN** inventory finds an old available backup
- **THEN** it remains available until an explicit point delete or separately specified prune policy is invoked

#### Scenario: Stopped cluster owns data

- **WHEN** a stopped SDK cluster retains its volume
- **THEN** resource tools report the retained data and do not implicitly destroy it
