## Why

OdCLI's checkout, Python environment, PostgreSQL, Odoo process, module, backup, restore, and cleanup paths are tested separately, but no required CI scenario proves that they work together against a real Odoo 19 Community server. A reproducible, secret-safe harness is needed now so regressions in the public workflow fail deterministically instead of being hidden by opt-in prerequisites or container shortcuts.

## What Changes

- Add a hybrid real-Odoo harness in which Docker Compose owns a disposable PostgreSQL cluster and pinned source Odoo server while the target Odoo 19 checkout, `uv` environment, configuration, process, and cleanup remain under OdCLI control.
- Add a deterministic `base`-only technical addon and generate a genuine ZIP backup containing database data and a filestore attachment from pinned inputs during each full run.
- Add one end-to-end critical path plus focused failure and idempotency cases, derived from the canonical `PUBLIC_LEAF_CASES` inventory rather than a second command registry.
- Extend the existing public environment synchronization contract with an explicit audited hash-lock mode so checkout and `env sync` can install an exact reviewed resolution through `uv pip sync --require-hashes` without a test-only discovery bypass or direct subprocess substitute.
- Add distinct PR smoke and scheduled/manual full-E2E CI tiers with pinned inputs, measured cold/warm budgets, sanitized failure evidence, and fail-closed prerequisite handling.
- Replace the prerequisite-only role of `tests/integration/test_real_odoo_lifecycle.py` with self-provisioning fixtures while preserving the existing offline, packaging, and dashboard suites.

## Capabilities

### New Capabilities

- `real-odoo-e2e-verification`: Reproducible orchestration, fixture data, public-workflow coverage, negative cases, cleanup, and CI evidence for a real Odoo 19 Community end-to-end harness.

### Modified Capabilities

- `development-environment`: Add the public, fail-closed hash-lock inputs and execution semantics used by checkout and `env sync` for an owned environment.
- `client-config`: Keep the canonical `OdooClient.environments` facade signature aligned with the additive hash-lock synchronization parameters.

## Impact

Implementation will affect the existing environment resource and CLI adapter, one neutral internal dependency-sync builder, their focused tests, real-Odoo integration tests and fixtures, CI workflows, test documentation, and development dependency metadata. The additive hash-lock parameters change an existing public operation without adding a public type or CLI leaf. The harness will reuse the existing Docker Compose and centralized process-execution boundaries; no runtime dependency, Enterprise source, private repository, browser test, or production runner is added.
