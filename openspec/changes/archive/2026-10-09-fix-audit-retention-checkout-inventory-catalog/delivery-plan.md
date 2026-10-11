## Delivery contract

- Task key: `MYL-423`
- OpenSpec change: `fix-audit-retention-checkout-inventory-catalog`
- Approved base: `origin/main` at `ab71895f7031eabe129fbd5dbfcda71298cd4016`
- Delivery mode: `single_wp_no_dag`
- Estimate source: authoritative planning-issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`, populated from `estimate-openspec` and verified by read-back.
- Estimate basis: remaining effort for one experienced developer familiar with Python/SQLite/Alembic, without AI acceleration; active development includes investigation, implementation, focused and repository-required checks, and likely review fixes. Unattended CI, human approval queues, and external blocking are excluded.
- Confidence: medium. Acceptance criteria and affected paths are explicit, all production/test paths were inspected, and close local regressions exist. The main uncertainty is platform-sensitive multiprocessing pipe behavior plus the exact historical-schema test shape. The estimate is evidence-based but uncalibrated because no comparable developer-duration history is available. Tests were not run for estimating.
- Evidence: current implementations in `backup_retention.py`, `checkout_api.py`, `checkout_stages.py`, `checkout_inventory.py`, and `catalog_migrate.py`; focused tests in `test_backup_resource.py`, `test_environment_adoption.py`, `test_checkout_inventory.py`, and `test_catalog_migration.py`; repository gates in `Makefile`; base revision and clean pre-authoring snapshot recorded above.

The authoritative estimate selects one work package. Operational assignment and live status are intentionally not stored in OpenSpec.

## WP-01 — Implement and verify all MYL-423 audit corrections

**Task coverage (complete and one-time):** `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`, `3.3`, `4.1`, `4.2`, `4.3`, `5.1`, `5.2`.

**Deliverable:** one production-ready change that fixes retention TOML table boundaries, unifies checkout/adoption provenance projection, prevents large provider responses from deadlocking or disappearing, centralizes exact historical catalogue fingerprints, and supplies focused plus repository-level verification evidence.

**Owned responsibility scope:**

- Retention settings patch/read behavior and its public resource regressions.
- Checkout planning/provenance normalization, adoption command projection, and ordinary/adoption characterization.
- Environment-facts child-process transport lifecycle, timeout/error isolation, and inventory regressions.
- Catalogue historical-schema recognition/repair, known fixtures, fail-closed behavior, and Alembic metadata checks.
- Directly related tests, fixtures, snapshots, documentation, generated contract artifacts, and repository check adjustments required by these changes.
- Critical shared files include `src/odoo_instance_sdk/internal/backup_retention.py`, `src/odoo_instance_sdk/resources/environment/checkout.py`, `checkout_api.py`, `checkout_stages.py`, `checkout_planning.py`, `src/odoo_instance_sdk/internal/checkout_inventory.py`, and `src/odoo_instance_sdk/storage/catalog_migrate.py`.

**Contract surface:**

- Existing SDK method names, CLI syntax, frozen public result types, retention defaults, adoption ownership rules, provider timeout budget, SQLite schema, and Alembic head remain stable.
- `user.toml` remains line-preserved outside the two owned retention assignments and parses with following array-of-tables intact.
- Ordinary checkout and adoption publish normalized provenance from the same captured source; adoption keeps its stronger repository/ownership gate.
- Provider collection returns only valid typed summaries, isolates failures, and terminates overdue children within the existing bounded cleanup contract.
- Only exact known historical catalogue shapes may be repaired/stamped/upgraded; unknown shapes fail before mutation.

**Definition of done / evidence:**

- Every OpenSpec task is checked only after its observable assertion passes.
- Focused tests prove parsed TOML ownership, public adoption provenance, large provider payload delivery plus existing timeout/error cases, and both catalogue historical paths including unknown-shape rejection.
- Ordinary checkout/adoption characterization remains compatible except for the intended resolved-base correction.
- Repository formatting, lint, typing, Alembic/schema, architecture/contract, and applicable test gates pass, or any unrelated/environment-limited failure is recorded with reproducible command and evidence.
- No new dependency, public API, generic framework, persisted schema revision, or generated visual HTML enters the Git diff.

**Execution safety:** a single implementer/verifier pair owns the complete coupled change, so shared planning and migration boundaries cannot diverge across sibling write zones.
