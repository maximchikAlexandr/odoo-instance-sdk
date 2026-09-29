## ADDED Requirements

### Requirement: Guarded drop accepts only current exact bootstrap origin

The existing CLI-private guarded project-cluster deletion operation SHALL accept a current bootstrap-origin binding as an alternative ownership proof only for the exact database `tmp` created by the Compose bootstrap flow. It SHALL preserve the completed exact restore-binding path for every restored database and SHALL add no public SDK database method, whole-cluster teardown, implicit ownership inference, or restore row.

Planning and immediate pre-mutation revalidation under the existing project-cluster lock SHALL establish equality among the canonical project, current active `cluster_id`, Compose project, expected named volume, inspected volume and attached-container labels, current endpoint, exact `tmp` name, and current bootstrap-origin binding. The operation SHALL also preserve all existing denylist, template, configured-default override, explicit destructive confirmation, environment/runtime binding, active-session, forced-connection, immutable-plan, redaction, and PostgreSQL absence-verification gates. Any missing, legacy, identity-null, foreign, malformed, stale, changed, or unreadable value SHALL fail closed before session termination, `DROP DATABASE`, audit reconciliation, bootstrap-origin mutation, or filestore mutation.

#### Scenario: Matching init-created tmp may be removed

- **WHEN** `odcli db rm tmp --force-default --yes` targets the exact stopped project whose active cluster, inspected volume/container labels, endpoint, and current bootstrap-origin binding all match, and all existing binding/session checks pass
- **THEN** the command drops only `tmp`, verifies its absence, reconciles the drop, retires only that bootstrap-origin binding, and evaluates only the proven contained `tmp` filestore

#### Scenario: Existing restore-origin behavior is unchanged

- **WHEN** the target has a completed exact restore binding rather than a current bootstrap-origin binding
- **THEN** the existing restore ownership and filestore rules authorize or refuse the operation exactly as before

#### Scenario: Legacy or manually existing tmp is refused

- **WHEN** `tmp` exists but has no current exact bootstrap-origin binding and no qualifying completed restore binding
- **THEN** the command performs no session termination, database drop, catalog reconciliation, origin write, or filestore mutation

#### Scenario: Foreign or mismatched bootstrap evidence is refused

- **WHEN** any recorded or inspected project, cluster, Compose project, volume, container attachment, endpoint, or database identity differs from the selected project and `tmp`
- **THEN** the command fails closed before every destructive or audit effect and does not adopt or rewrite the evidence

#### Scenario: Active use remains refused

- **WHEN** an active environment, confirmed live project runtime, or non-forced database session uses the bootstrap-origin `tmp`
- **THEN** the command preserves the existing refusal and performs no destructive or catalog mutation

#### Scenario: Default override and confirmation remain mandatory

- **WHEN** `tmp` is the configured project default or machine-readable invocation lacks the existing explicit confirmation
- **THEN** bootstrap provenance does not bypass `--force-default` or `--yes`, and the command fails before mutation

#### Scenario: Ownership changes before mutation

- **WHEN** bootstrap ownership passed planning but immediate locked revalidation finds the claim, labels, endpoint, origin binding, default selection, binding state, or sessions changed or unavailable
- **THEN** no session is terminated, no database is dropped, and no audit, origin, or filestore state is changed

### Requirement: Bootstrap origin lifecycle cannot authorize a replacement database

Bootstrap-origin evidence SHALL represent only the current SDK bootstrap lifecycle. A completed SDK restore to the same exact cluster/database SHALL atomically supersede the bootstrap origin so later deletion must satisfy restore provenance. After PostgreSQL absence is verified, catalog reconciliation SHALL atomically record the idempotent dropped event and retire the matching bootstrap-origin binding. If the process stops after PostgreSQL deletion but before reconciliation, an idempotent retry that proves the target absent and revalidates the same ownership SHALL complete that catalog finalization without deleting any other resource.

#### Scenario: Restore supersedes bootstrap origin

- **WHEN** an SDK restore completes for `tmp` on the same exact active cluster
- **THEN** the restore transaction records its normal completed provenance and retires the bootstrap-origin binding, so a later drop requires the restore binding

#### Scenario: Successful drop retires bootstrap authority

- **WHEN** the guarded command verifies that bootstrap-origin `tmp` is absent after its drop
- **THEN** one catalog transaction records the canonical idempotent dropped event and removes only the matching bootstrap-origin binding

#### Scenario: Retry finalizes an already absent target

- **WHEN** a prior authorized mutation removed `tmp` but catalog finalization did not complete, and retry revalidates the same exact bootstrap ownership while PostgreSQL proves `tmp` absent
- **THEN** retry performs no `DROP DATABASE`, atomically reconciles the dropped event and retires that binding, and applies only the existing proven-filestore cleanup rules

#### Scenario: Replacement database has no stale bootstrap authority

- **WHEN** `tmp` is later restored or recreated after its prior bootstrap-origin binding was retired
- **THEN** the retired binding cannot authorize deletion and the database requires new qualifying current origin evidence
