## Delivery Contract

- Task key: `MYL-397`
- OpenSpec change: `fix-backup-checkout-probe-consumption`
- Approved base: `origin/main` at `c1e57b79f39e529a50c25818134c06309384ee23`
- Delivery mode: `single_wp_no_dag`
- Estimate source: authoritative root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numeric totals are intentionally not duplicated here.
- Estimate basis: remaining active developer effort for one experienced developer familiar with Python, Click, pytest, and this repository, without AI acceleration. It includes investigation, implementation, required tests, verification, and likely review repairs; unattended CI, approval queues, meetings, and external waiting are excluded.
- Confidence: medium. The duplicate call and authoritative replacement path are directly inspectable, and close checkout fixtures already exist. Uncertainty is concentrated in replacing mock-heavy explicit-source coverage with a real `DatabaseResource` plus recording/fake executor and in sharing that harness with named-remote acquisition.
- Calibration: evidence-based but uncalibrated; no comparable historical actuals were available. Tests were not run for the estimate.

The authoritative estimate property is within the protocol threshold that requires one all-covering work package. Production and regression edits intentionally remain one unit because they share the same command snapshot, coordinator, executor fixtures, and acceptance evidence.

## WP-MYL-397-01 — Single-consumption explicit-source COPY restore

**Task coverage (complete and one-time):** `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `2.4`, `2.5`, `3.1`, and `3.2` from `tasks.md`. Every OpenSpec task is assigned here exactly once; none is omitted or duplicated.

**Independent deliverable:** selected-catalog-backup and named-remote COPY checkout preserve artifact-free existing-target preflight, then consume the existing coordinator-owned `database.restore.exists-before` and `database.restore.exists-after` steps exactly once around verified-absence restore. Public dry-run and real execution use the same immutable plan, successful checkout reaches the established typed ready result, and an existing target still fails before restore without changing borrowed backup state.

**Owned responsibility scope:** the COPY database-preparation coordinator in `src/odoo_instance_sdk/resources/environment/settings.py`; directly related environment checkout resource and public CLI/SDK boundary tests, fixtures, fake/recording executor support, snapshots, typing adjustments, and documentation needed to prove the accepted contract. The package may make the smallest directly related changes required by observed test failures, but it does not own unrelated restore, catalog, runner, or lifecycle refactors.

**Critical shared files:** `src/odoo_instance_sdk/resources/environment/settings.py`, `tests/unit/resources/test_environment_checkout.py`, and any existing CLI checkout boundary test module whose fixture is reused for public command proof.

**Contract surface:** retain `_preflight_copy_checkout()` as the pre-mutation target guard; retain `_consume_copy_database_probe()` as the sole execution-time before/after consumer; retain existing step IDs and order in the immutable checkout command; retain `_restore_after_verified_absence()` and `DatabaseAlreadyExistsError`; preserve selected/named backup identity, borrowed ownership, copy journal stages, provenance, locks, rollback, redaction, and owned-artifact cleanup; do not introduce a third probe, source-specific runner/context, catalog bypass, public type, dependency, or schema change.

**Definition of done / evidence:** focused public-command regressions exercise a real `DatabaseResource` through the repository recording/fake executor; selected-backup success proves the exact two probe IDs are consumed once each and verified-absence restore runs once; named-remote coverage proves the same coordinator contract; existing-target coverage proves typed preflight rejection and no restore/overwrite/borrowed-backup mutation; dry-run/execution plan equality and probe ordering are asserted; existing journal, provenance, rollback, redaction, locking, and cleanup tests remain green. Repository formatting/lint, strict typing, standard offline tests, strict OpenSpec validation, and `git diff --check` pass with commands and exit codes recorded.

**Execution rationale:** the implementation is a narrow removal in one coordinator, while the material work is one coherent public-boundary regression harness over the same command snapshot and fixtures. Splitting those responsibilities would create overlapping writes and duplicate validation without an independently useful deliverable.

## Coverage Proof

`WP-MYL-397-01` covers the complete task set `1.1–3.2` once. The task identifiers listed above reconcile exactly with `tasks.md`; no execution responsibility remains outside this package.

## Estimation Evidence and Assumptions

- Inspected implementation: `_prepare_copy_database()`, `_preflight_copy_checkout()`, `_consume_copy_database_probe()`, `DatabaseResource.exists()`, restore verified-absence transport, and checkout step construction.
- Inspected verification seams: retained-backup and named-remote checkout tests, existing exact-probe assertions, `RecordingExecutor`/fake process patterns, journal/provenance assertions, and repository quality commands.
- The estimate assumes the accepted design remains a removal of the redundant nested existence call plus focused regression work, with no new public API, catalog migration, live Odoo/PostgreSQL test, dependency, or execution abstraction.
- The estimate must be revisited if the real-resource regression reveals that named-remote acquisition requires production architecture changes, if acceptance expands to live-service end-to-end evidence, or if the immutable plan/typed error contract must change.
