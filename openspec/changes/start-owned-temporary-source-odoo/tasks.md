## 1. Auxiliary Database Manager Lifecycle

- [ ] 1.1 Add focused lifecycle regressions for purpose-specific step IDs, exact selected source `StartConfig`, project executable/cwd/environment reuse, secret-safe public projection, and unchanged stopped-project restore behavior.
- [ ] 1.2 Generalize the internal restore helper into a purpose-aware auxiliary Database Manager session while preserving the restore facade, step IDs, process registration, readiness, same-project ownership proof, bounded termination, secret cleanup, and unused-step skipping.
- [ ] 1.3 Add a COPY source-session builder that combines the exact captured source config with the resolved project runtime, ownership binding, cluster dependency, default run arguments, and project environment without adding public API surface.

## 2. COPY Checkout Integration

- [ ] 2.1 Extend COPY checkout command construction so the immutable prepared ledger contains conditional `checkout.source-odoo.auxiliary.start`, `.ready`, and `.cleanup` steps around the existing database phase, while non-COPY plans and dry-run remain non-mutating and secret-safe.
- [ ] 2.2 Change COPY preflight to use the selected source instance, start or reuse the captured helper only after `DatabaseManagerUnavailableError`, retry the source-name probe exactly once, preserve other error classifications, and reject foreign/stale listeners before durable checkout mutation.
- [ ] 2.3 Keep the selected helper session alive through the existing `_do_copy_restore()` backup/restore/postcondition pipeline and guarantee final cleanup on success, failure, and interruption without stopping or unregistering a reused runtime.
- [ ] 2.4 Preserve existing COPY journal and compensation ordering after preflight, including target non-overwrite, owned backup retention/deletion, restored-target uncertainty, failed/cleanup-failed state, and primary-error diagnostics when helper cleanup also fails.

## 3. Verification

- [ ] 3.1 Add COPY checkout regressions for an already-responsive source, stopped-source success, live same-project runtime reuse, foreign listener rejection, startup failure, readiness failure, failed retry, post-start backup/restore failure, cleanup failure, exact step ordering, and no durable preflight artifacts.
- [ ] 3.2 Add CLI/public-command parity and dry-run assertions proving the same captured ledger is used, helper steps are redacted, no helper is started during inspection, and shared-mode behavior is unchanged.
- [ ] 3.3 Run focused auxiliary/checkout tests, the full unit suite, formatting and lint checks, strict typing, and repository architecture/public-surface gates; reconcile documentation only if existing user-facing behavior descriptions or generated inventories require it.
