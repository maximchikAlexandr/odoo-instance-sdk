## 1. Shared auxiliary lifecycle

- [x] 1.1 [T01] Move the existing auxiliary-command attachment helper from `commands/db.py` into the instance auxiliary-runtime layer, preserve database restore step ordering, and update both import/export boundaries.
- [x] 1.2 [T02] Extend the auxiliary session to reuse a responsive Database Manager or a proven compatible recorded runtime without registering or stopping it, while retaining fail-closed handling for an unknown occupied port.
- [x] 1.3 [T03] Make the shared attachment cleanup preserve an active primary exception, report cleanup failure as attached diagnostics, reset the active session in every path, and still fail when cleanup alone fails.

## 2. COPY checkout integration

- [x] 2.1 [T04] Build an auxiliary source instance/session from the captured project runtime and source configuration only for COPY checkout, then attach it to the existing immutable checkout command before COPY preflight/catalog mutation.
- [x] 2.2 [T05] Keep auxiliary start, readiness, and cleanup in both public and private checkout step sequences so dry-run exposes the sanitized possible lifecycle without spawning, registering, signalling, writing secrets, or mutating checkout state.
- [x] 2.3 [T06] Preserve the existing COPY preflight, target-absence probes, journal transitions, backup provenance, restore postconditions, rollback/compensation behavior, and leave the shared-mode command and plan unchanged.

## 3. Verification

- [x] 3.1 [T07] Add focused auxiliary-session tests for responsive external reuse, recorded-runtime reuse, unknown occupied listener rejection, owned start/readiness success, timeout or early-exit cleanup, and primary-versus-cleanup error precedence.
- [x] 3.2 [T08] Add COPY checkout command tests covering stopped-source success, plan/private-step parity, non-spawning dry-run, cleanup after preflight/backup/restore failure and cancellation, and no lifecycle change in shared mode.
- [x] 3.3 [T09] Run focused pytest for auxiliary restore and environment checkout, then run Ruff, strict mypy, the full unit suite, strict OpenSpec validation, and `git diff --check`; resolve every in-scope failure without weakening existing safety checks.
