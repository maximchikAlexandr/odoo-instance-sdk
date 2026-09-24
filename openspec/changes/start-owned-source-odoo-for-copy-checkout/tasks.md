## 1. Shared auxiliary lifecycle

- [x] 1.1 [T01] Move the existing auxiliary-command attachment helper from `commands/db.py` into the instance auxiliary-runtime layer, preserve database restore step ordering, and update both import/export boundaries.
- [ ] 1.2 [T02] Remove response-shaped unrecorded listener reuse; reuse only a persisted compatible runtime whose exact PID/create-time/process identity owns the configured live listening socket, fail closed when socket proof is absent or unverifiable, and retain free-port-only owned startup.
- [x] 1.3 [T03] Make the shared attachment cleanup preserve an active primary exception, report cleanup failure as attached diagnostics, reset the active session in every path, and still fail when cleanup alone fails.

## 2. COPY checkout integration

- [x] 2.1 [T04] Build an auxiliary source instance/session from the captured project runtime and source configuration only for COPY checkout, then attach it to the existing immutable checkout command before COPY preflight/catalog mutation.
- [ ] 2.2 [T05] Add distinct recorded-identity, port/ownership, and per-privileged-request revalidation actions to the same public/private prepared sequence; consume, fail, complete, or skip them around the exact in-process effects while keeping dry-run sanitized and inert.
- [x] 2.3 [T06] Preserve the existing COPY preflight, target-absence probes, journal transitions, backup provenance, restore postconditions, rollback/compensation behavior, and leave the shared-mode command and plan unchanged.

## 3. Verification

- [ ] 3.1 [T07] Add focused auxiliary-session tests for exact listener-PID binding, mismatched/ambiguous/uninspectable socket rejection, responsive unrecorded fail-closed behavior, recorded and owned runtime request-adjacent revalidation, and preservation of existing startup/readiness/cleanup/error-precedence cases.
- [ ] 3.2 [T08] Add COPY and standalone restore regressions proving backup/restore HTTP is not invoked and `master_pwd` is not sent after identity drift; assert unique action ids/order, public/private plan parity, inert dry-run, source-instance scoping, and unchanged shared/target behavior.
- [ ] 3.3 [T09] Run focused auxiliary, backup/restore, checkout, and public lifecycle pytest; run Ruff, strict mypy, the full unit suite, strict OpenSpec validation, and `git diff --check`; resolve every in-scope failure without weakening identity, secret, plan, or recovery checks.
