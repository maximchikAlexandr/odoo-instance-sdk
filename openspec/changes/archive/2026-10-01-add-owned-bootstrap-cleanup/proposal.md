## Why

Self-contained Compose initialization creates the disposable `tmp` database but records no database-origin evidence that the guarded PostgreSQL drop path accepts. As a result, a normal SDK-created database is intentionally refused by every supported cleanup command and users must leave OdCLI to dispose of it.

## What Changes

- Extend the existing append-only `database_events` lifecycle with `bootstrapped`, recorded only after the SDK-created `tmp` database passes its existing SQL readiness check on an active owned cluster.
- Allow the existing guarded `odcli db rm` path to accept `bootstrapped` only when it is the latest exact event for `tmp`, without weakening restore-origin checks for ordinary databases.
- Revalidate the event, project/cluster/volume/container identity, configured-default override, active bindings, and sessions under the existing cluster lock immediately before mutation.
- Reuse the existing idempotent `record_database_dropped` reconciliation after successful deletion and in the authorized already-absent retry path, so later `restored` or `dropped` events naturally revoke bootstrap authority.
- Add the bounded CHECK-constraint migration, command-plan, unit, CLI, disposable Compose regression, and user documentation needed for the supported cleanup.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: Successful Compose bootstrap appends exact `bootstrapped` lifecycle evidence for `tmp`; failed, skipped, dry-run, external, legacy, or mismatched initialization does not fabricate it.
- `database-management`: The guarded project-cluster drop accepts a latest exact `bootstrapped` event for `tmp` while preserving every existing destructive-operation refusal and restore-origin rule.

## Impact

- Existing `database_events` schema and one Alembic CHECK-constraint migration; no new table or parallel ownership store.
- Compose init and first-run bootstrap execution/audit actions.
- Internal guarded PostgreSQL drop ownership projection plus the existing dropped-event reconciliation; no new public SDK database method, finalizer, or teardown command.
- Existing CLI confirmation, `--force-default`, `--force-connections`, immutable command-plan, process-boundary, redaction, and typed-output contracts remain in force.
- Tests around catalog migration/equivalence, bootstrap execution, database-drop ownership matrices, CLI output, and opt-in disposable Compose behavior; database lifecycle documentation gains the supported cleanup command.
