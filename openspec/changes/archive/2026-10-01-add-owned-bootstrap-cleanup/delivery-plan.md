## Delivery Contract

- **Task key:** `MYL-349`
- **Change:** `add-owned-bootstrap-cleanup`
- **Approved base:** `origin/main` at the planning baseline recorded in the publication commit ancestry
- **Delivery mode:** `single_wp_no_dag`
- **Sizing source:** authoritative `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` custom properties on the root planning issue; numeric totals are intentionally not duplicated here
- **Estimate basis:** remaining work for one experienced developer familiar with Python, Click, SQLite/Alembic, PostgreSQL, Docker Compose, and this repository, without AI acceleration; active development hours include investigation, implementation, required tests, review fixes, and attended verification, while external queues and unavailable opt-in infrastructure are excluded
- **Confidence:** medium and uncalibrated; the acceptance behavior and affected paths are inspectable, while catalog table rebuild compatibility, lifecycle ordering, interrupted-retry reconciliation, and disposable Compose verification can create bounded repair work
- **Packaging rule:** the authoritative estimate selects one all-covering work package. The package owns the complete change end to end so schema, bootstrap publication, guarded deletion, tests, and documentation evolve under one implementation/review boundary.

No implementation work may weaken fail-closed behavior, adopt legacy resources, invent restore provenance, introduce a second lifecycle store or cleanup path, or change the public SDK method set. Directly related tests, fixtures, snapshots, documentation, migration metadata, and service files belong to the package.

## Task Coverage

Every OpenSpec task is owned exactly once:

| OpenSpec tasks | Work package |
|---|---|
| `1.1`, `1.2`, `2.1`, `2.2`, `3.1`, `3.2`, `4.1`, `4.2` | `WP-01-owned-bootstrap-cleanup` |

## WP-01-owned-bootstrap-cleanup

- **OpenSpec task coverage:** `1.1`, `1.2`, `2.1`, `2.2`, `3.1`, `3.2`, `4.1`, `4.2`
- **Deliverable:** a migrated append-only lifecycle log that records newly created and ready `tmp` databases as `bootstrapped`, a bootstrap executor that distinguishes creation from already-ready reuse, and the existing guarded database-drop flow extended to accept only a latest exact matching bootstrap event and to append `dropped` after successful or already-absent deletion.
- **Owned responsibility scope:** `database_events` schema and Alembic migration; catalog append/latest-event/drop-recording helpers; bootstrap outcome and init/first-run orchestration; existing guarded-drop ownership planning, locked revalidation, and absent reconciliation; focused catalog, migration, bootstrap, drop, CLI, and public-surface tests; disposable Compose regression; database lifecycle documentation; directly related fixtures and migration metadata. Critical shared surfaces include `storage/catalog_schema.py`, `storage/catalog_migrations/`, `storage/catalog/restore.py`, `internal/dbprep/bootstrap.py`, `internal/pg/drop.py`, their callers, and corresponding tests.
- **Contract surface:** `database_events.event_type` admits `bootstrapped` alongside `restored` and `dropped`. A bootstrap event carries the existing exact endpoint, database, cluster, and data-directory identity and is appended only after this invocation created `tmp` and readiness succeeded. Guarded deletion reads the last exact event by append sequence: matching `bootstrapped` may authorize only `tmp`; `restored` follows the existing restore binding path; `dropped` or no event refuses. The same ownership projection is revalidated under the existing lock. Both successful and authorized already-absent paths call the existing idempotent `record_database_dropped`; later lifecycle events naturally supersede earlier authority.
- **DoD / evidence:** fresh and prior-head migrations reach one metadata-equivalent head while preserving rows, sequence, indexes, and foreign keys; invalid lifecycle payloads are rejected; created bootstrap appends only after readiness while already-ready, dry-run, external, failed, and legacy paths do not adopt; exact latest-event ownership succeeds and missing, stale, malformed, foreign, mismatched, active, bound, or connected cases refuse before destructive effects; planning and locked evidence must match; interrupted retry reconciles with one `dropped` event; restore behavior and the public SDK method inventory remain unchanged; focused unit/CLI tests, public-boundary disposable Compose regression, documentation, formatting, lint, typing, repository tests, and strict OpenSpec validation provide completion evidence.
- **Coordination rationale:** one owner is required across the lifecycle constraint, bootstrap publication, and destructive retry semantics because each changes the meaning of the same ordered event stream. Keeping the complete task set in one package avoids a temporary or review-time mismatch between writers and the guarded reader while remaining within the authoritative sizing threshold.

## Estimation Evidence and Assumptions

- Evidence inspected: append-only lifecycle schema and ordering in `storage/catalog_schema.py`; restore/drop event publication and latest-event lookup in `storage/catalog/restore.py`; shared bootstrap readiness flow in `internal/dbprep/bootstrap.py`; the existing twice-validated ownership gate in `internal/pg/drop.py`; current migration rebuild patterns and equivalence tests; unit/CLI safety matrices; opt-in disposable Compose coverage; repository format/lint/type/test gates; and the revised OpenSpec scenarios.
- The estimate includes the constraint migration and compatibility repair, narrow catalog helpers, bootstrap outcome propagation, guarded-drop extension, focused and integration tests, documentation, required review fixes, and attended verification.
- It assumes the current PostgreSQL drop transport, cluster lock, active-binding detection, confirmation flags, contained-filestore helper, Compose label inspection, and `record_database_dropped` idempotency remain reusable as designed.
- It assumes no requirement for a new catalog relation, bootstrap CRUD lifecycle, custom atomic finalizer, whole-cluster reset, legacy `tmp` adoption, external PostgreSQL bootstrap cleanup, a new public SDK method, or automatic runtime shutdown.
- Main uncertainty is coupled around the `database_events` CHECK-constraint rebuild, exact latest-event interpretation, and the already-absent retry branch; public-boundary Docker availability affects elapsed verification but not the required implementation scope.
- The estimate is invalidated by expanding scope to volume teardown, legacy adoption/migration, multiple bootstrap database names, external clusters, or a new public cleanup API.
- Tests were not run to manufacture estimate evidence; existing code, tests, configuration, and history were read instead.
