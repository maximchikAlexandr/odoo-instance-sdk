## 1. Dependency and identity contract

- [x] 1.1 Promote `psutil` to the core dependency, remove the obsolete metrics extra, and regenerate `uv.lock`.
- [x] 1.2 Remove optional-process imports and approximate runtime identity; persist exact process create time.
- [x] 1.3 Add regression coverage for exact foreground identity accepted by monitor process collection.

## 2. Foreground lifecycle safety

- [x] 2.1 Reap the owned process group on an unexpected foreground wait exception before cleanup and re-raise.
- [x] 2.2 Add a long-running owned-process regression for exceptional wait cleanup.

## 3. PostgreSQL CLI isolation

- [x] 3.1 Add `-X` and scrub ambient psql/libpq startup variables while retaining password-file authentication.
- [x] 3.2 Add transport regressions for argv and inherited-environment behavior.

## 4. Catalog aggregation and verification

- [x] 4.1 Ensure monitor collection uses the atomic environment/runtime catalog aggregate and cover it.
- [x] 4.2 Validate OpenSpec, run focused and repository verification gates, and record results.

## Verification evidence

2026-08-25, after the final implementation changes:

- `openspec validate require-core-psutil --strict` — PASS.
- Focused lifecycle/identity and serve tests — 38 passed; the identity test uses
  a live subprocess and its unmocked `psutil.Process(pid).create_time()`.
- `make lint`, `make types`, and `make test` — PASS (788 parallel + 11 serial).
- Dockerless `make compat` — PASS (788 + 11).
- `make dashboard`, `make smoke`, and `make package` — PASS (28 dashboard
  Python tests, 2 Vitest tests, 1 smoke test, 6 packaging tests).
- `osv-scanner` for `uv.lock` (56 packages) and web `package-lock.json`
  (235 packages), plus `npm audit --omit=dev --audit-level=high` — no issues.
- `git diff --check` — PASS.

2026-08-25, final lifecycle follow-up:

- The serial POSIX integration regression starts an owned leader that exits and
  a same-group child that ignores `SIGTERM`; a simulated foreground wait error
  confirms bounded group liveness, `SIGKILL` escalation, leader reaping, and
  child/group disappearance.
- `make lint`, `make types`, `make test`, `make compat`, `make dashboard`,
  `make smoke`, and `make package` — PASS.
- Dependency manifests and lockfiles are unchanged by this follow-up, so OSV
  and npm audits were not rerun; the previous clean scan remains applicable.

2026-08-25, review20 closure follow-up:

- `run_psql()` now scrubs `PGHOST` and `PGHOSTADDR` as well as startup inputs,
  while retaining `PGPASSFILE`; transport tests cover socket and explicit TCP
  argv modes.
- The Ctrl+C foreground path reuses the captured-PGID bounded group cleanup.
  A serial POSIX integration test synchronizes an ignored-SIGINT/SIGTERM leader
  and descendant, then proves exit 130, handler restoration, reaping, and
  group disappearance.
- Packaging tests inspect the built wheel `METADATA` and enforce the direct
  core psutil requirement, exact dashboard extra set, no metrics extra, and no
  accidental core dependencies.
