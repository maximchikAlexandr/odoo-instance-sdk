## Delivery Contract

- **Task key:** `MYL-349`
- **Change:** `add-owned-bootstrap-cleanup`
- **Approved base:** `origin/main` at the planning baseline recorded in the publication commit ancestry
- **Delivery mode:** `dag`
- **Sizing source:** authoritative `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` custom properties on the root planning issue; numeric totals are intentionally not duplicated here
- **Estimate basis:** remaining work for one experienced developer familiar with Python, Click, SQLite/Alembic, PostgreSQL, Docker Compose, and this repository, without AI acceleration; active development hours include investigation, implementation, required tests, review fixes, and attended verification, while external queues and unavailable opt-in infrastructure are excluded
- **Confidence:** medium and uncalibrated; acceptance behavior and affected paths are inspectable, but the cross-transaction crash/retry behavior, catalog migration compatibility, and disposable Compose proof can create bounded repair work
- **Topology rationale:** the authoritative estimate selects DAG mode. After one catalog contract predecessor, bootstrap publication and guarded deletion have separate production and test write zones and can execute concurrently. The final end-to-end/documentation package depends on both.

No implementation WP may weaken fail-closed behavior, adopt legacy resources, invent restore provenance, introduce a second cleanup path, or change the public SDK method set. Directly related tests, fixtures, snapshots, documentation, migration metadata, and service files belong to the responsible WP unless this plan assigns them to an active sibling.

## Task Coverage

Every OpenSpec task is owned exactly once:

| OpenSpec tasks | Work package |
|---|---|
| `1.1`, `1.2`, `1.3` | `WP-01-catalog-bootstrap-origin` |
| `2.1`, `2.2`, `2.3` | `WP-02-bootstrap-publication` |
| `3.1`, `3.2`, `3.3`, `3.4` | `WP-03-guarded-drop-origin` |
| `4.1`, `4.2`, `4.3` | `WP-04-e2e-docs-verification` |

## Stage Map

This mapping is authoritative for child issue materialization:

| `wp_id` | `stage` |
|---|---:|
| `WP-01-catalog-bootstrap-origin` | 1 |
| `WP-02-bootstrap-publication` | 2 |
| `WP-03-guarded-drop-origin` | 2 |
| `WP-04-e2e-docs-verification` | 3 |

Stages are consecutive topological layers. Stage 2 contains the parallel execution frontier; WIP limits are operational policy and are not encoded as dependency edges.

## WP-01-catalog-bootstrap-origin

- **Stage:** 1
- **OpenSpec task coverage:** `1.1`, `1.2`, `1.3`
- **Direct `depends_on`:** none
- **Deliverable:** an additive, migrated, metadata-equivalent catalog relation and validated operations that publish/read/retire exact bootstrap origin, atomically finalize a bootstrap-authorized drop, and supersede bootstrap origin during completed restore publication.
- **Owned responsibility scope:** catalog schema metadata; the next Alembic head and migration verification; catalog bootstrap-origin model/projection and persistence methods; the existing restore transactions only where they must atomically retire matching bootstrap evidence; focused catalog and migration tests plus directly related fixtures. Critical shared surfaces include `storage/catalog_schema.py`, `storage/catalog_migrations/`, `storage/catalog/`, `storage/catalog_migrate.py`, and catalog test support.
- **Contract surface:** expose one typed internal bootstrap-origin value and narrow catalog methods whose inputs carry exact project/cluster/Compose/volume/endpoint/database/data-directory identity. Publication requires an active matching claim and fixed `tmp`; retirement/finalization uses compare-and-delete identity and one transaction; completed restore retires only the same exact cluster/database binding.
- **DoD / evidence:** fresh catalog and prior-head migration reach one head and metadata equivalence; no legacy bootstrap rows are fabricated; pending, malformed, foreign, and mismatched claims fail; exact publication is idempotent; compare-and-delete preserves replacement rows; restore supersession and dropped-event/origin retirement are atomic; focused catalog tests and static checks pass.
- **Parallel-safety rationale:** foundation WP owns every shared persistence contract before consumers start. It has no sibling at stage 1.

## WP-02-bootstrap-publication

- **Stage:** 2
- **OpenSpec task coverage:** `2.1`, `2.2`, `2.3`
- **Direct `depends_on`:** `WP-01-catalog-bootstrap-origin`
- **Deliverable:** init and first foreground run publish bootstrap authority only when their captured spawn actually creates `tmp` and readiness succeeds; already-ready, dry-run, external, failed, and identity-mismatched paths remain non-authoritative.
- **Owned responsibility scope:** shared bootstrap executor and typed outcome; init Compose follow-up orchestration; first-run/foreground bootstrap orchestration; immutable `PreparedAction` planning and action consumption; bootstrap/init/foreground tests and their local helpers. Critical shared surfaces include `internal/dbprep/bootstrap.py`, `project_init.py`, `resources/instance/identity.py`, `tests/unit/test_init_self_contained.py`, and `tests/unit/test_cli_init_postgres.py`.
- **Contract surface:** consume WP-01's bootstrap-origin publisher without changing its persistence API. Return a typed internal `created` or `already_ready` result; record only on `created` after readiness; present and consume the same conditional catalog action in preview/execution; preserve `init_bootstrap_failed`, immutable argv, redaction, and process-boundary rules.
- **DoD / evidence:** plans show the conditional action; created flow writes exact origin; already-ready never adopts; dry-run/external/failure never writes; publication failure makes the command fail closed; init and first-run callers share one helper; focused bootstrap/init/runtime tests and static checks pass.
- **Parallel-safety rationale:** after WP-01 freezes catalog APIs, this WP writes only bootstrap/init/runtime orchestration and its test zone. It does not edit guarded-drop production/tests owned by WP-03.

## WP-03-guarded-drop-origin

- **Stage:** 2
- **OpenSpec task coverage:** `3.1`, `3.2`, `3.3`, `3.4`
- **Direct `depends_on`:** `WP-01-catalog-bootstrap-origin`
- **Deliverable:** the existing guarded database-drop command accepts the exact current bootstrap origin as a discriminated alternative for `tmp`, revalidates it under the existing lock, finalizes successful or already-absent deletion atomically, and leaves all restore and public SDK behavior unchanged.
- **Owned responsibility scope:** internal PostgreSQL drop ownership projection and execution/finalization branch; existing CLI callback only where sanitized origin projection or unchanged confirmation wiring requires it; focused drop ownership, CLI output, public-method inventory, and safety matrix tests. Critical shared surfaces include `internal/pg/drop.py`, the `commands/db.py` guarded-drop adapter, `tests/unit/internal/test_postgres_drop.py`, `tests/unit/commands/test_db_drop.py`, and relevant `test_cli_output_modes.py` cases.
- **Contract surface:** consume WP-01's exact reader and atomic finalizer. Ownership becomes an exhaustive restore/bootstrap union; bootstrap accepts only fixed `tmp` with exact claim/labels/endpoint/data-directory identity; planning and locked revalidation projections must compare equal; absence retry invokes the same identity-bound finalizer; restore variant and public SDK surface remain byte-for-behavior compatible.
- **DoD / evidence:** matching bootstrap identity succeeds; missing/legacy/foreign/malformed/mismatched evidence refuses before effects; active environment/runtime/session and default/confirmation policies remain enforced; changed evidence at execution refuses; already-absent retry reconciles without a second drop; retired origin cannot authorize replacement; restore matrices, typed output, redaction, and public inventory remain green.
- **Parallel-safety rationale:** after WP-01, this WP owns drop/CLI surfaces and their tests only. It does not edit bootstrap/init/runtime production/tests owned by WP-02. Any catalog API deficiency is returned to the predecessor contract rather than patched concurrently.

## WP-04-e2e-docs-verification

- **Stage:** 3
- **OpenSpec task coverage:** `4.1`, `4.2`, `4.3`
- **Direct `depends_on`:** `WP-02-bootstrap-publication`, `WP-03-guarded-drop-origin`
- **Deliverable:** public-boundary disposable Compose evidence, user documentation, and the final focused/repository verification ledger for the integrated change.
- **Owned responsibility scope:** disposable PostgreSQL/Compose integration scenario and its cleanup helpers; database lifecycle/CLI user documentation; cross-surface fixtures or snapshots not owned by active predecessors; final focused test selection, formatter, lint, typing, strict OpenSpec validation, and opt-in environment result recording.
- **Contract surface:** exercise the shipped CLI command `odcli db rm tmp --force-default --yes`, with `--force-connections` only under its existing explicit policy. Use normal init/bootstrap/catalog evidence rather than inserting fake restore provenance. The mismatch case changes one identity component and asserts zero destructive side effects outside the exact target.
- **DoD / evidence:** public-boundary success proves only matching `tmp` is removed and absence is reconciled; mismatch proves no termination, drop, volume deletion, unrelated filestore deletion, or adoption; documentation states stopped/exact-owned prerequisites and refusal behavior; focused tests and repository gates pass; strict OpenSpec validation passes; the opt-in Compose result is recorded as passed or as unavailable with its documented prerequisite, never silently omitted.
- **Parallel-safety rationale:** this is the convergence package and starts only after both stage-2 deliverables exist. It owns integration/docs and final evidence, avoiding concurrent edits to either predecessor's primary production surfaces.

## Dependency and Frontier Check

```text
stage 1: WP-01-catalog-bootstrap-origin
             ├──> stage 2: WP-02-bootstrap-publication ──┐
             └──> stage 2: WP-03-guarded-drop-origin ────┼──> stage 3: WP-04-e2e-docs-verification
```

The stage-2 frontier contains two independent WPs with non-conflicting write zones. Dependencies are direct only: WP-04 names both immediate producers, and neither stage-2 sibling depends on the other.

## Estimation Evidence and Assumptions

- Evidence inspected: current bootstrap capture/execution in `internal/dbprep/bootstrap.py` and its init/foreground callers; guarded ownership and twice-validated mutation in `internal/pg/drop.py`; active cluster claim persistence and exact volume/container inspection; restore transactions and current Alembic single-head/equivalence tests; unit/CLI/drop matrices; opt-in disposable Compose coverage; repository format/lint/type/test gates; and analogous recent catalog/runtime identity work in local history.
- The estimate includes catalog migration and compatibility repair, implementation, focused and integration tests, documentation, required review fixes, and attended verification.
- It assumes the current PostgreSQL drop transport, cluster lock, active-binding detection, confirmation flags, contained-filestore helper, and Compose label inspection remain reusable as designed.
- It assumes no requirement to support whole-cluster reset, legacy `tmp` adoption, external PostgreSQL bootstrap cleanup, a new public SDK method, or automatic runtime shutdown.
- Main uncertainty is coupled across WP-01 and both consumers: atomic origin lifecycle semantics may expose migration or retry edge cases; public-boundary Docker availability affects elapsed verification but not the required implementation scope.
- The estimate is invalidated by expanding scope to volume teardown, legacy adoption/migration, multiple bootstrap database names, external clusters, or a new public cleanup API.
- Tests were not run to manufacture estimate evidence; existing code, tests, configuration, and history were read instead.
