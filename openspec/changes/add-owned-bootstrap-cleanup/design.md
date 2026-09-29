## Context

The current Compose bootstrap path builds and executes one captured Odoo spawn for `tmp`, proves `base` readiness through PostgreSQL, and then returns. The catalog already carries authoritative project and active PostgreSQL cluster claims, while guarded `db rm` accepts database-level ownership only from the latest completed restore. That restore-only rule is correctly fail-closed, but bootstrap creates `tmp` outside it.

The change crosses bootstrap execution, catalog schema/migration, restore finalization, guarded deletion, tests, and user documentation. It must preserve the repository's immutable command-plan/process boundary and must not turn declarative Compose configuration into ownership evidence.

## Goals / Non-Goals

**Goals:**

- Give a freshly created, stopped, exactly owned Compose project a supported `odcli db rm tmp` cleanup path.
- Record database-level bootstrap origin only after the existing creation and readiness proof succeeds.
- Reuse the mature guarded database-drop operation and all of its revalidation, locking, confirmation, binding, session, audit, and filestore controls.
- Ensure stale bootstrap evidence cannot authorize a restored, recreated, foreign, or otherwise replacement database.
- Keep planning/preview output inspectable and secret-free.

**Non-Goals:**

- No whole-project or Docker-volume reset command.
- No weakening of the exact restore-origin gate for ordinary databases.
- No adoption or backfill of pre-existing/legacy `tmp` databases.
- No public `DatabaseResource` or `PostgresCluster` cleanup method and no second catalog.
- No automatic stopping of an active Odoo runtime and no implicit connection termination.

## Decisions

### D1. Add a distinct current bootstrap-origin relation

Add a small catalog relation keyed by exact `(cluster_id, database_name)` and containing canonical `project_id`, Compose project, volume name, normalized database host/port, fixed origin kind `bootstrap`, project-owned data directory, and creation time. A fresh-schema definition and one Alembic head migration SHALL remain metadata-equivalent.

Catalog methods SHALL validate that the referenced cluster claim is currently `active`, matches project/Compose/volume identity, and that the name is exactly `tmp`. Bootstrap evidence is not stored in `restores`; its semantics and lifecycle are different.

Alternatives considered:

- Treat bootstrap as a restore source kind: rejected because no restore occurred and it would falsify the existing restore contract.
- Infer ownership from `mode="compose"`, the database name, or a successful probe: rejected because those facts are reproducible by foreign and legacy resources.
- Add whole-cluster teardown: rejected as a broader destructive surface that also needs independent volume, filestore, runtime, and multi-database policy.

### D2. Publish origin only for creation performed in the captured bootstrap execution

The internal bootstrap result SHALL distinguish `created` from `already_ready`. The plan adds one conditional catalog `ActionStep` after readiness verification. Only `created` proceeds to a catalog writer supplied with the already captured project/cluster/data-directory identity; `already_ready` skips the action. Both init and first-run callers use the same helper and action contract.

The writer re-reads the active cluster claim at commit time. If catalog publication fails, the command fails; later readiness alone does not backfill authority. This deliberately leaves crash-orphaned databases fail-closed.

Alternative considered: record every successful readiness probe. Rejected because it silently adopts databases created outside the SDK.

### D3. Extend the existing ownership projection with a discriminated origin

The guarded drop ownership projection SHALL represent either `restore` or `bootstrap` evidence. The shared prefix remains unchanged: resolve the project-bound Compose cluster, load the exact active claim, reject active catalog bindings, and inspect exact volume plus container attachment labels.

For `restore`, retain all current backup/source/digest and contained-filestore checks. For `bootstrap`, require the current relation to match the active claim, project, Compose project, expected volume, endpoint, exact `tmp`, and contained project-owned data directory. The same projection is calculated during planning and again inside the existing cluster lock; inequality refuses mutation.

This is the ponytail/minimal path: one extra evidence variant at the existing root ownership gate, not a parallel cleanup implementation.

### D4. Make restore and drop finalization revoke bootstrap authority

A completed restore transaction for the exact cluster/database SHALL delete any matching bootstrap-origin relation as it records normal restore provenance. It does not touch bootstrap records for other clusters or names.

After verified deletion, replace the split audit call for this path with one catalog transaction that performs the existing idempotent `dropped` event reconciliation and conditionally deletes the exact bootstrap record using the identity observed during locked revalidation. An already-absent retry runs the same finalizer after ownership and absence checks. A changed or missing row fails closed instead of deleting a replacement record.

Alternative considered: leave the bootstrap row and prefer the newest timestamp. Rejected because timestamps across relations are an avoidable ordering ambiguity and stale authority would remain durable.

### D5. Keep the public and CLI surface stable

Users use the existing command and flags: `odcli db rm tmp --force-default --yes`, adding `--force-connections` only when they explicitly accept termination under the existing policy. Public SDK method discovery and typed CLI output remain unchanged. Preview enriches the existing ownership projection with sanitized `origin=bootstrap`; it exposes no password or raw secret.

### D6. Verify at catalog, bootstrap, ownership, CLI, and disposable Compose boundaries

Tests SHALL cover fresh-schema/migration equivalence; exact record/revoke validation; created versus already-ready bootstrap behavior; immutable plan/action consumption; restore supersession; planning and locked revalidation mismatch matrices; active environment/runtime/session and default/confirmation refusals; successful and interrupted-finalization retries; unchanged restore-origin behavior; and a public CLI/SDK-boundary disposable Compose regression with recorded catalog/label evidence.

The opt-in Compose test creates the normal bootstrap `tmp`, stops Odoo, invokes the public CLI path, and proves only the matching identity succeeds. A second case changes one ownership component and proves no session termination, `DROP DATABASE`, volume deletion, or filestore deletion occurs.

## Risks / Trade-offs

- **Catalog publication can fail after PostgreSQL creation** → fail the command and keep the database unauthorized; never repair ownership from a later probe.
- **A stale current-origin row could authorize a replacement** → revoke it atomically on restore and drop finalization, compare exact identity twice, and require new evidence after recreation.
- **Schema migration increases delivery surface** → add one narrow table, one head migration, downgrade behavior consistent with current migration policy, and metadata-equivalence tests.
- **Bootstrap filestore may be absent** → preserve the existing contained-path cleanup semantics; absence is successful, unknown or unsafe paths are retained and reported.
- **Retry after partial success could diverge catalog and PostgreSQL** → the already-absent branch performs identity-bound idempotent finalization and cannot issue a second drop.
- **First-run and init paths could drift** → both consume the same bootstrap outcome and catalog-action helper rather than duplicating origin logic.

## Migration Plan

1. Ship the catalog table and Alembic migration; existing rows remain unchanged and no legacy bootstrap evidence is synthesized.
2. Publish bootstrap evidence only on new successful SDK creation after the upgraded code is running.
3. Enable the additional guarded-drop evidence variant and restore/drop revocation in the same release so no durable origin lacks lifecycle handling.
4. Rollback removes the additive relation through the migration's explicit downgrade policy; existing restore and drop behavior remains usable, while bootstrap-only cleanup returns to fail-closed behavior.

## Open Questions

None. The selected scope is the exact init-created `tmp` cleanup through existing `db rm`; whole-cluster reset remains outside this change.
