## Delivery Mode

`single_wp_no_dag`

The authoritative `Estimate, hours` property on planning issue `MYL-238` falls within the mandatory single-work-package threshold. The estimate has medium confidence: the existing auxiliary restore session, COPY checkout pipeline, and focused lifecycle tests are strong local analogues; the bounded uncertainty is in composing an explicit source config with project runtime identity and preserving primary-failure versus cleanup-failure diagnostics. Exact estimate totals and range are stored only in the issue's authoritative estimate properties.

## WP-01 — Owned Temporary Source Odoo for COPY Checkout

- Tasks covered exactly once: `1.1`–`1.3`, `2.1`–`2.4`, and `3.1`–`3.3` from `tasks.md`.
- `depends_on`: none.
- Deliverable: COPY checkout transparently recovers from a stopped local source Database Manager by conditionally starting one bounded SDK-owned source Odoo, using it across the existing backup/restore flow, and cleaning it up without changing public APIs or weakening lifecycle safety.
- Owned responsibility scope: the auxiliary Database Manager lifecycle under `src/odoo_instance_sdk/resources/instance/`; COPY planning, immutable step construction, preflight, execution, and rollback under `src/odoo_instance_sdk/resources/environment/`; directly related command adapters only if parity requires adjustment; and associated unit/integration tests, fixtures, architecture inventories, snapshots, and user-facing documentation required by the change.
- Critical shared files: `src/odoo_instance_sdk/resources/instance/auxiliary_restore.py`, `src/odoo_instance_sdk/resources/instance/__init__.py`, `src/odoo_instance_sdk/resources/environment/checkout.py`, `src/odoo_instance_sdk/resources/environment/checkout_artifacts.py`, `src/odoo_instance_sdk/resources/environment/settings.py`, `tests/unit/test_stopped_project_restore.py`, and `tests/unit/resources/test_environment_checkout.py`.
- Contract surface: existing `EnvironmentResource.checkout*` methods and `EnvironmentCheckoutPlan`; the prepared-command/process boundary; purpose-specific auxiliary session steps; exact selected source `StartConfig`; project runtime ownership and endpoint checks; `DatabaseManagerUnavailableError` fallback; existing `DatabaseResource.names()`, `backup()`, and restore paths; COPY journal, compensation, provenance, and target postconditions.
- Definition of done / evidence: every requirement and scenario in `specs/development-environment/spec.md` maps to a focused passing regression; stopped-project restore compatibility remains green; COPY and shared command ledgers retain exact ordering and redacted projections; inspection/dry-run creates no process or durable state; foreign listeners are neither used nor stopped; owned cleanup runs on success, failure, and interruption; format, lint, strict typing, full offline tests, coverage, architecture/public-surface checks, and strict OpenSpec validation pass; the implementation branch descends from the approved base and finishes clean.
- Parallel-safety rationale: this change is one tightly coupled process-lifecycle and checkout-ledger modification. Splitting it would create overlapping ownership of the helper abstraction, command step ordering, and COPY failure cleanup, so there are no sibling work packages or parallel write zones.

## Coverage Audit

The package contains ten implementation tasks. WP-01 covers every task exactly once: three auxiliary-lifecycle tasks, four COPY integration tasks, and three verification tasks. No task is uncovered or duplicated.
