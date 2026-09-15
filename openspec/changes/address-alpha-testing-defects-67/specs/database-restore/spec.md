## ADDED Requirements

### Requirement: COPY-restore preserves managed PostgreSQL cluster identity

When a COPY environment restore is performed, the active managed PostgreSQL cluster identity SHALL be bound on the `OdooInstance` used for the copy restore (through the same `_postgres_cluster` binding used by `from_environment()`) so that `record_restore()` receives and stores a `cluster_id` matching the active owned cluster claim. The existing `record_restore()` call SHALL receive the cluster identity from the bound instance; no new parameter is added to the public `record_restore()` signature. Absence of a claim SHALL NOT be substituted with a guess. The guarded direct PostgreSQL drop SHALL then work for fresh COPY environments because `restores.cluster_id` matches the active managed cluster.

When guarded drop-plan construction fails, the command SHALL report a sanitized primary reason and SHALL NOT swallow the failure into an uninformative `None` while retaining fail-closed behavior. Checks for dirty worktree, active runtime, cluster/volume identity, restore binding, and related resources SHALL NOT be weakened.

#### Scenario: Fresh COPY-restore records cluster_id

- **WHEN** a COPY checkout restores a fresh backup on a managed cluster
- **THEN** the new restore row has a `cluster_id` matching the active owned cluster claim

#### Scenario: Env rm shows guarded drop step

- **WHEN** `odcli env rm ENVIRONMENT_UUID --dry-run` runs for such a COPY environment
- **THEN** the plan contains the guarded database drop step

#### Scenario: Drop-plan failure reports primary reason

- **WHEN** guarded drop-plan construction fails
- **THEN** the command reports a sanitized primary reason instead of an uninformative `None`