## Delivery contract

- Task key: `MYL-350`.
- OpenSpec change: `reconcile-incomplete-project-lifecycle`.
- Approved base: `origin/main` at `d810693cf0665fb680fb670f78770e8c056c66d9`.
- Delivery mode: `dag`.
- Topology basis: authoritative optimistic, weighted, and pessimistic totals are stored only in the root planning issue properties `Estimate min, hours`, `Estimate, hours`, and `Estimate max, hours`, with successful read-back. The weighted property is above the single-package threshold, and the first frontier contains two naturally independent write zones.
- Estimate basis: remaining effort to all acceptance scenarios for one experienced developer familiar with this Python/Click/msgspec/SQLAlchemy/Alembic repository, without AI acceleration. Unattended CI, approval queues, meetings, and external blocking are excluded.
- Confidence: medium. The affected init, preparation, catalogue, generated-config, guarded-drop, and test paths are inspectable and have close local analogues. Uncertainty is concentrated in migration equivalence, post-failure process-ledger consumption, precise retry semantics, and compensation/error preservation across two owned files.
- Calibration: uncalibrated; no comparable active-hour records were available. Tests were not run for estimation.

## Execution topology

```text
Stage 1: WP-MYL-350-01 catalogue foundation ──→ Stage 2: WP-MYL-350-03 restore/config reconciliation ──┐
Stage 1: WP-MYL-350-02 init resume ─────────────────────────────────────────────────────────────────────┴→ Stage 3: WP-MYL-350-04 integrated gate
```

Stage mapping: `WP-MYL-350-01 -> 1`, `WP-MYL-350-02 -> 1`, `WP-MYL-350-03 -> 2`, `WP-MYL-350-04 -> 3`. Stages are contiguous topological levels. `WP-MYL-350-01` and `WP-MYL-350-02` form a real parallel frontier with disjoint production and focused-test ownership. `WP-MYL-350-03` depends only on the catalogue contract; `WP-MYL-350-04` joins both completed behavior paths.

## WP-MYL-350-01 — Restore-state catalogue foundation

**Task coverage:** `2.1`.

**Deliverable:** One linear catalogue revision and typed storage contract distinguish complete from incomplete restore bindings without changing historical completed-restore meaning.

**depends_on:** none.

**stage:** `1`.

**Owned responsibility scope:** restore schema/model/codec/query changes, Alembic revision, declarative metadata, historical fixtures, and directly related migration/schema-equivalence tests. Critical shared files include `src/odoo_instance_sdk/storage/catalog_schema.py`, `src/odoo_instance_sdk/storage/catalog/restore.py`, `src/odoo_instance_sdk/storage/catalog_migrate.py`, `src/odoo_instance_sdk/storage/catalog_migrations/`, and catalogue migration tests/fixtures. Directly related generated schema bookkeeping belongs to this WP.

**Contract surface:** restore bindings expose a validated `complete|incomplete` state; new ordinary restore writes default explicitly to complete; historical rows upgrade as complete; latest/list queries return state without changing endpoint/database/source/cluster identity; downgrade preserves the legacy complete-only representation.

**Definition of done / evidence:** fresh-schema, upgrade, downgrade, historical-fixture, single-head, declarative/runtime metadata equivalence, invalid-state, and complete-default tests pass; strict typing and formatting pass for touched files.

**Parallel-safety rationale:** this package exclusively owns catalogue schema and restore persistence. Its sibling owns init CLI/project-init code and does not write catalogue files or migration fixtures.

## WP-MYL-350-02 — Idempotent Compose init completion

**Task coverage:** `1.1`, `1.2`, `1.3`.

**Deliverable:** Identical Compose init resumes or verifies the existing captured cluster/bootstrap phases before success, while complete and non-Compose cases retain their minimal no-op/repair behavior and truthful output metadata.

**depends_on:** none.

**stage:** `1`.

**Owned responsibility scope:** existing-manifest decision, init command construction/execution, Compose/bootstrap follow-up selection, CLI init registration/callback behavior, and directly related init/output tests. Critical shared files include `src/odoo_instance_sdk/project_init.py`, `src/odoo_instance_sdk/commands/cli_parts/callbacks.py`, `src/odoo_instance_sdk/commands/cli_parts/init_registration.py`, `src/odoo_instance_sdk/internal/project_init.py`, and focused init/CLI output fixtures. Catalogue and database-preparation files are excluded.

**Contract surface:** typed existing-manifest outcome; lock-bound equality revalidation; no repeat manifest mutation for resume; existing immutable ensure-running/bootstrap steps; SQL-only acceptance for `tmp`; exact image/cluster trust; no recreation after successful SQL proof; requested `dry_run` value in no-op envelopes.

**Definition of done / evidence:** public CLI/SDK regressions cover first-call failure after durable intent, successful identical retry, complete retry, repeated boundary failure, config repair, non-Compose compatibility, Rich/JSON/TOON parity, and immutable step consumption; architecture inventory, focused tests, Ruff, and strict mypy pass.

**Parallel-safety rationale:** this package writes only init and init-specific CLI/test seams. It shares no production write zone with the catalogue foundation sibling.

## WP-MYL-350-03 — Exact restore recovery and coherent default switch

**Task coverage:** `2.2`, `2.3`, `2.4`, `2.5`, `3.1`, `3.2`, `3.3`.

**Deliverable:** A failed restore publishes exact incomplete ownership only after captured owned-cluster proof, supported retry/drop reconciles that evidence without weakening guards, and successful default switching leaves the manifest and project-owned generated config coherent.

**depends_on:** `WP-MYL-350-01`.

**stage:** `2`.

**Owned responsibility scope:** database-preparation materialization/failure context, captured existence-probe consumption, restore inventory projections, guarded drop/retry behavior, project-owned generated-config/default-switch helper and compensation, and focused public-boundary tests. Critical shared files include `src/odoo_instance_sdk/internal/dbprep/`, `src/odoo_instance_sdk/internal/pg/inventory.py`, `src/odoo_instance_sdk/internal/pg/drop.py`, `src/odoo_instance_sdk/internal/project_init.py`, database preparation/database CLI projections, and their directly related unit/integration tests. The predecessor's schema/migration files are read-only after handoff.

**Contract surface:** affirmative exact `exists-after` proof plus active cluster/source identity creates one incomplete binding; unknown proof creates none; incomplete state cannot satisfy success/default/readiness; retry/drop revalidate cluster, endpoint, target, provenance, volume, active use, and containment; successful drop retires evidence; default switch preflights owned config, preserves unrelated settings and external config, compensates ordinary write failures, and returns success only after both owned files agree.

**Definition of done / evidence:** failure-after-create, probe unavailable/negative, catalogue and local-archive provenance, inventory state, exact retry, exact drop, unrelated refusal, evidence drift, active-use race, filestore containment, successful generated-config synchronization, next-run selection, external-config preservation, manifest drift, and injected compensation failures pass through existing SDK/CLI boundaries. Plan-step parity, secret redaction, focused tests, Ruff, and strict mypy pass.

**Parallel-safety rationale:** the package begins after restore-state storage is frozen and is the sole writer to preparation/drop/generated-config seams. It does not edit the sibling init callbacks or init command implementation.

## WP-MYL-350-04 — Documentation and integrated delivery gate

**Task coverage:** `4.1`, `4.2`.

**Deliverable:** User/SDK documentation and canonical inventories describe the supported recovery flows, and one integrated verification set proves all OpenSpec scenarios without parallel registries or weakened checks.

**depends_on:** `WP-MYL-350-02`, `WP-MYL-350-03`.

**stage:** `3`.

**Owned responsibility scope:** README/docs/changelog updates, canonical architecture/output inventories, cross-domain integration tests and fixtures, and final repairs required by repository verification after both behavior packages land. Critical shared files include documentation, `PUBLIC_LEAF_CASES`/architecture inventories, full-flow integration fixtures, and any directly related snapshots. Production repairs are limited to defects exposed by the integrated gates and must preserve predecessor contracts.

**Contract surface:** documented identical-init recovery, truthful no-op output, incomplete-binding diagnosis/retry/drop, fail-closed unknown database behavior, generated-config synchronization, and explicit crash-window limitation; no new public command or recovery bypass.

**Definition of done / evidence:** every OpenSpec scenario is traceable to a passing focused or integrated test; catalogue migration gates, init/preparation/drop/output suites, architecture/schema checks, Ruff format/check, strict mypy, full non-real-Odoo suite, `git diff --check`, and strict OpenSpec validation pass. Required real-Odoo cases run only when the repository's explicit prerequisites are available and otherwise remain represented by deterministic boundary tests.

**Parallel-safety rationale:** this join package starts only after both behavior branches are complete and exclusively owns shared documentation/inventory/integration repairs, eliminating sibling conflicts.

## Task coverage proof

| Work package | OpenSpec tasks covered exactly once |
| --- | --- |
| `WP-MYL-350-01` | `2.1` |
| `WP-MYL-350-02` | `1.1`, `1.2`, `1.3` |
| `WP-MYL-350-03` | `2.2`, `2.3`, `2.4`, `2.5`, `3.1`, `3.2`, `3.3` |
| `WP-MYL-350-04` | `4.1`, `4.2` |

Every checkbox in `tasks.md` is assigned exactly once. The stage-1 frontier contains two independent packages with non-overlapping write zones, so the graph has genuine parallelism and does not collapse into a linear chain. Operational WIP and assignees are intentionally not stored in OpenSpec.

## Estimation evidence and invalidation conditions

- Evidence: current identical-manifest early return in `commands/cli_parts/callbacks.py`; reusable Compose/bootstrap steps in `project_init.py`; captured before/after PostgreSQL probes in `internal/dbprep/materialize_steps.py`; delayed `database_confirmed` and failure context in `internal/dbprep/materialize.py`/`source_binding.py`; restore bindings and active-cluster validation in `storage/catalog/restore.py`; exact guarded drop in `internal/pg/drop.py`; generated-config writer/repair logic in `internal/project_init.py`; existing migration, CLI output, and lifecycle test matrices.
- Assumptions: the existing restore record remains the authoritative recovery binding with one added state; retry uses existing collision/ownership gates rather than a new command; no public manifest migration or external service is required; deterministic fake-boundary tests cover mandatory acceptance while opt-in real-Odoo evidence follows existing repository policy.
- Main uncertainty: preserving the primary restore exception when probe/catalog recovery also fails, migration downgrade semantics for unresolved incomplete rows, and compensation behavior across filesystem failures.
- A requirement for automatic cleanup, recovery of targets whose existence cannot be proved, a new public command/schema, cross-host restore coordination, or crash-proof multi-file transactions invalidates the estimate properties and requires a planning revision.
