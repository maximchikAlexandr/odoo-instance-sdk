## Why

Self-contained Compose initialization creates the disposable `tmp` database but records no database-origin evidence that the guarded PostgreSQL drop path accepts. As a result, a normal SDK-created database is intentionally refused by every supported cleanup command and users must leave OdCLI to dispose of it.

## What Changes

- Record a distinct, exact bootstrap-database origin only after the SDK-created `tmp` database has passed its existing SQL readiness check on an active owned cluster.
- Allow the existing guarded `odcli db rm` path to accept that bootstrap origin as an alternative to completed restore provenance, without weakening restore-origin checks for ordinary databases.
- Revalidate the bootstrap origin, project/cluster/volume/container identity, configured-default override, active bindings, and sessions under the existing cluster lock immediately before mutation.
- Reconcile successful deletion so stale bootstrap evidence cannot authorize a later database with the same name.
- Add bounded catalog-migration, command-plan, unit, CLI, and disposable Compose regression coverage plus user documentation for removing an init-created `tmp` database.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: Successful Compose bootstrap records exact origin evidence for `tmp`; failed, skipped, dry-run, external, legacy, or mismatched initialization does not fabricate it.
- `database-management`: The guarded project-cluster drop accepts a current exact bootstrap origin for `tmp` while preserving every existing destructive-operation refusal and restore-origin rule.

## Impact

- Catalog schema and Alembic migration for bootstrap-origin lifecycle evidence.
- Compose init and first-run bootstrap execution/audit actions.
- Internal guarded PostgreSQL drop ownership projection and reconciliation; no new public SDK database method and no new teardown command.
- Existing CLI confirmation, `--force-default`, `--force-connections`, immutable command-plan, process-boundary, redaction, and typed-output contracts remain in force.
- Tests around catalog migration/equivalence, bootstrap execution, database-drop ownership matrices, CLI output, and opt-in disposable Compose behavior; database lifecycle documentation gains the supported cleanup command.
