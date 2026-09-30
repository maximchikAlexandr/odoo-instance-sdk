## ADDED Requirements

### Requirement: Database lifecycle records bootstrap origin

The existing append-only `database_events` lifecycle SHALL support event type `bootstrapped`. Its exact endpoint, database name, `cluster_id`, and data directory SHALL use the existing columns; restore-only fields SHALL be null. The catalog SHALL append it only for database `tmp` after validating that the referenced cluster claim is active. The latest exact event SHALL be selected by descending global sequence.

The additive Alembic migration SHALL update the existing SQLite CHECK constraints without creating a new relation, preserve every historical event and sequence, restore both existing indexes and foreign-key behavior, keep fresh-schema metadata equivalent to the migration head, and synthesize no `bootstrapped` event. Downgrade SHALL fail closed while any `bootstrapped` row exists rather than discard audit history.

#### Scenario: Fresh bootstrap event is current

- **WHEN** a validated `bootstrapped` event is appended after existing history for the exact endpoint and `tmp`
- **THEN** the latest-event reader returns that event with its exact cluster/data-directory evidence and null restore fields

#### Scenario: Restore and drop naturally supersede bootstrap

- **WHEN** the existing restore or dropped-event reconciliation later appends `restored` or `dropped` for the same endpoint and `tmp`
- **THEN** ordinary sequence ordering makes that later event current without updating or deleting the historical `bootstrapped` row

#### Scenario: Migration preserves existing history

- **WHEN** a catalog at the prior head containing restored and dropped events is upgraded
- **THEN** all rows, sequences, indexes, foreign-key behavior, and provenance constraints remain intact, no bootstrap evidence is fabricated, and metadata matches the fresh schema

#### Scenario: Invalid bootstrap payload is refused

- **WHEN** bootstrap event publication targets a name other than `tmp`, a missing or non-active cluster claim, an empty data directory, or restore provenance fields
- **THEN** the catalog refuses the append without changing lifecycle history

### Requirement: Guarded drop accepts only a latest exact bootstrap event

The existing CLI-private guarded project-cluster deletion operation SHALL accept bootstrap origin as an alternative ownership proof only when `bootstrapped` is the latest exact event for database `tmp`. It SHALL preserve the completed exact restore-binding path whenever `restored` is current and SHALL add no public SDK database method, whole-cluster teardown, implicit ownership inference, new origin table, or restore row.

Planning and immediate pre-mutation revalidation under the existing project-cluster lock SHALL establish equality among the latest exact event, its non-null `cluster_id`, the current active project claim, Compose project, expected named volume, inspected volume and attached-container labels, endpoint, exact `tmp` name, and safe recorded data directory. The operation SHALL preserve all existing denylist, template, configured-default override, explicit destructive confirmation, environment/runtime binding, active-session, forced-connection, immutable-plan, redaction, and PostgreSQL absence-verification gates. A latest `dropped` event, missing event, or any legacy, identity-null, foreign, malformed, stale, changed, or unreadable value SHALL fail closed before session termination, `DROP DATABASE`, reconciliation, or filestore mutation.

#### Scenario: Matching init-created tmp may be removed

- **WHEN** `odcli db rm tmp --force-default --yes` targets the exact stopped project whose latest event is matching `bootstrapped`, whose active cluster and inspected volume/container labels match that event, and whose remaining binding/session checks pass
- **THEN** the command drops only `tmp`, verifies its absence, appends the existing idempotent `dropped` event, and evaluates only the proven contained `tmp` filestore

#### Scenario: Current restored origin remains unchanged

- **WHEN** the latest exact event is `restored`
- **THEN** the existing completed restore-binding and filestore rules authorize or refuse the operation exactly as before

#### Scenario: Later lifecycle event revokes bootstrap authority

- **WHEN** an older `bootstrapped` event is followed by `restored` or `dropped` for the same endpoint and `tmp`
- **THEN** the older event cannot authorize deletion and the gate applies the current restore path or refuses the dropped origin respectively

#### Scenario: Legacy or manually existing tmp is refused

- **WHEN** `tmp` exists but its latest exact event is absent, unknown, or not qualifying `bootstrapped`/`restored`
- **THEN** the command performs no session termination, database drop, reconciliation, event append, or filestore mutation

#### Scenario: Foreign or mismatched bootstrap evidence is refused

- **WHEN** the event `cluster_id`, endpoint, database, data directory, active project claim, Compose project, volume, or container attachment differs from the selected project and `tmp`
- **THEN** the command fails closed before every destructive or audit effect and does not adopt or rewrite evidence

#### Scenario: Active use remains refused

- **WHEN** an active environment, confirmed live project runtime, or non-forced database session uses bootstrap-origin `tmp`
- **THEN** the command preserves the existing refusal and performs no destructive or catalog mutation

#### Scenario: Default override and confirmation remain mandatory

- **WHEN** `tmp` is the configured project default or a machine-readable invocation lacks the existing explicit confirmation
- **THEN** bootstrap evidence does not bypass `--force-default` or `--yes`, and the command fails before mutation

#### Scenario: Ownership changes before mutation

- **WHEN** bootstrap ownership passed planning but locked revalidation finds the latest event, claim, labels, endpoint, default selection, binding state, or sessions changed or unavailable
- **THEN** no session is terminated, no database is dropped, and no audit or filestore state is changed

#### Scenario: Interrupted drop retry records absence

- **WHEN** an authorized prior mutation removed `tmp` but did not append `dropped`, retry revalidates the same latest `bootstrapped` evidence under lock, and PostgreSQL proves `tmp` absent
- **THEN** retry performs no `DROP DATABASE`, calls the existing idempotent `record_database_dropped`, and applies only the existing proven-filestore cleanup rules
