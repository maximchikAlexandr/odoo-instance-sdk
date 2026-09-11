## Why

OdCLI's checkout, Python environment, PostgreSQL, Odoo process, module, backup, restore, and cleanup paths are tested separately, but no required CI scenario proves that they work together against a real Odoo 19 Community server. A reproducible, secret-safe harness is needed now so regressions in the public workflow fail deterministically instead of being hidden by opt-in prerequisites or container shortcuts.

## What Changes

- Add a hybrid real-Odoo harness in which Docker Compose owns a disposable PostgreSQL cluster and pinned source Odoo server while the target Odoo 19 checkout, `uv` environment, configuration, process, and cleanup remain under OdCLI control.
- Add a deterministic `base`-only technical addon and generate a genuine ZIP backup containing database data and a filestore attachment from pinned inputs during each full run.
- Add one end-to-end critical path plus focused failure and idempotency cases, derived from the canonical `PUBLIC_LEAF_CASES` inventory rather than a second command registry.
- Add distinct PR smoke and scheduled/manual full-E2E CI tiers with pinned inputs, measured cold/warm budgets, sanitized failure evidence, and fail-closed prerequisite handling.
- Replace the prerequisite-only role of `tests/integration/test_real_odoo_lifecycle.py` with self-provisioning fixtures while preserving the existing offline, packaging, and dashboard suites.

## Capabilities

### New Capabilities

- `real-odoo-e2e-verification`: Reproducible orchestration, fixture data, public-workflow coverage, negative cases, cleanup, and CI evidence for a real Odoo 19 Community end-to-end harness.

### Modified Capabilities

- None. This change verifies existing public behavior and does not alter an OdCLI product contract.

## Impact

Implementation will affect real-Odoo integration tests and fixtures, CI workflows, test documentation, and development dependency metadata. The harness will reuse the existing Docker Compose and centralized process-execution boundaries; no runtime dependency, public SDK type, CLI leaf, Enterprise source, private repository, browser test, or production runner is added.
