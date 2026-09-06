## Why

Several bounded `odcli` operations can currently report success after a failed transaction finalization, delay opted-in process output until completion, or provide no useful progress while they run. At the same time, downloaded backups, restored databases, filestores, and other SDK-owned disk resources lack a complete safe CLI lifecycle, so operators cannot reliably inspect, restore, or reclaim them without bypassing the SDK's execution and audit contracts.

## What Changes

- Make the shared Odoo shell wrapper transaction-safe: user-code failure always attempts rollback, requested commit happens only after successful user code, and commit/rollback failures remain distinct visible failures.
- Correct `deps verify` for both uv selectors and explicit Python paths, and make distribution conflicts or missing imports consistently fail every output mode and the process exit status.
- Add lifecycle progress for the identified long bounded commands, a true real-time opt-in subprocess stream that preserves every canonical structural and captured-runtime secret detector across arbitrary chunks, bounded timeout tails, and one concise Rich completion line derived from `OutputDocument`.
- Stream remote backup responses directly into the existing `.part` artifact while calculating size and checksum, and make interruption return exit 130 with known retained-resource context.
- Add CLI backup list/show/validate/delete operations over exact catalogue UUIDs, including deterministic filtering/pagination, idempotent audit-preserving deletion, active-use protection, and containment/symlink checks.
- Establish a retry-safe `pending`→`active` ownership handshake for an `odcli`-created Compose cluster; keep both restore paths compatible with external and legacy Compose targets by recording nullable ownership provenance, then add `db restore <BACKUP_UUID>` over the existing preparation pipeline, side-effect-free cluster inventory, and guarded drop that requires the active catalogue identity, inspected volume label, and exact completed restore row to match, with filestore disposition only after that gate passes.
- Add a read-only resource inventory and doctor view for backups, databases, filestores, environments, logs, and owned cluster volumes, preserving unknown ownership and measurement limitations instead of guessing or deleting.
- Isolate all CLI init tests from the real user catalogue with a shared temporary fixture.
- Sequence delivery so transaction, dependency, progress, streaming, interruption, and test-isolation P0 work lands before the backup/database/resource lifecycle slices.
- Keep policy-driven pruning, log rotation, and full PostgreSQL cluster destruction outside this change until the point-delete, ownership, and measurement evidence required by GitHub #55 exists.

## Capabilities

### New Capabilities

- `local-resource-lifecycle`: Read-only inventory and diagnosis of SDK-owned or related local disk resources, their relationships, ownership confidence, and reclaimability.

### Modified Capabilities

- `command-execution`: Define real-time observed process output, streaming redaction, bounded timeout diagnostics, logical progress events, and interruption closure without adding another executor.
- `cli-odcli`: Add backup/database/resource leaves; align success, exit, progress, interruption, confirmation, machine-output, and concise Rich completion contracts.
- `server-lifecycle`: Make scripted Odoo shell transaction finalization explicit and failure-safe for every existing shell consumer.
- `development-environment`: Expose truthful progress for checkout and sync through the existing immutable command and observer contracts.
- `local-odoo-testing`: Expose truthful progress and interruption behavior for top-level and module-test execution.
- `project-database-preparation`: Reuse one download/restore pipeline for remote refresh and registered local backups, with atomic default switching and retained-resource failure context.
- `database-backup`: Stream remote archives to disk with incremental size/checksum enforcement and observable transfer progress.
- `backup-catalog`: Support exact UUID lookup, complete state-aware listing/history, active lifecycle references, and safe audit-preserving point deletion.
- `database-restore`: Restore a registered local backup repeatedly into distinct new databases while preserving validation, neutralization, audit, and postconditions.
- `database-management`: Inventory the authoritative project PostgreSQL cluster without Odoo-list side effects and reconcile database/filestore deletion only after verified outcomes.
- `environment-monitor`: Reuse current metrics to inventory related disk resources and describe incomplete or orphaned artifacts without automatic cleanup.
- `postgres-cluster`: Expose owned volume identity and reclaimability for resource reporting while retaining `stop` as non-destructive.

## Impact

The change affects the shared process executor and canonical redaction boundary, Odoo shell framing, bounded CLI output/rendering, environment and test commands, database preparation, backup/database resources and catalogue schema, monitor/storage-footprint collection, PostgreSQL inspection/drop integration, and their tests and documentation. Public CLI gains `backup`, `db list`, `db restore`, and `resource` leaves; JSON/TOON remain one envelope. UUID resolution, project-cluster inventory, local-restore orchestration, cluster ownership evidence, and resource projection remain CLI-private/internal compositions over the existing public SDK methods and catalogue, including reuse of `BackupResource.delete_command` and `validate_command` after exact internal UUID resolution. Cluster creation uses one recoverable catalogue claim whose random identity is persisted as non-authoritative `pending` before Compose effects and promoted to authoritative `active` only after readiness and exact label/attachment inspection. Both existing remote refresh restore and new catalogue restore remain available for external PostgreSQL bindings and legacy Compose targets with no authoritative claim: their completed audit provenance records `cluster_id=null`/unknown. A verified active managed target instead records its exact non-null identity. Nullable restore history is valid but never ownership proof and therefore cannot authorize `db drop`; direct deletion requires the non-null restore identity to match both the active claim and inspected `odcli`-labeled volume. A pending managed creation blocks restore until activation, while absence of a claim on an external or legacy target does not. Host, port, database name, manifest mode, and Compose project name are never ownership proof by themselves. The canonical public-method inventory and `test_discovered_public_methods` expectation SHALL remain unchanged. Catalogue migrations must preserve all backup and restore history, treat identity-less rows as unknown, destructive tests must use disposable resources, and no implementation may introduce a second executor, catalogue, event store, output model, restore pipeline, or background garbage collector.
