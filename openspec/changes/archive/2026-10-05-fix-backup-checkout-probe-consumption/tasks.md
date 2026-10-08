## 1. Coordinator Correction

- [x] 1.1 Remove the named-remote/selected-backup `instance.databases.exists(target_db)` call from `_prepare_copy_database()` so the coordinator-owned `database.restore.exists-before` step is the only execution-time absence probe.
- [x] 1.2 Preserve `_preflight_copy_checkout()` as the artifact-free existing-target rejection and preserve `_consume_copy_database_probe()` immediately before and after `_restore_after_verified_absence()` without adding or renaming planned steps.

## 2. Public Boundary Regression Coverage

- [x] 2.1 Add a selected-catalog-backup checkout fixture that exercises the public immutable checkout command with a real `DatabaseResource` and the repository's recording/fake process executor, without a live Odoo or PostgreSQL service.
- [x] 2.2 Prove successful selected-backup execution consumes `database.restore.exists-before` and `database.restore.exists-after` exactly once each, invokes verified-absence restore once, preserves borrowed-backup identity/journal state, and returns the established ready result.
- [x] 2.3 Cover the named-remote explicit-source path with the same coordinator-owned probe contract, using `pytest.mark.parametrize` for shared source/input expectations where the existing fixtures permit and otherwise one focused named-remote regression.
- [x] 2.4 Prove an existing target fails during artifact-free preflight with `DatabaseAlreadyExistsError`, never invokes restore, never overwrites the target, and retains a selected or downloaded borrowed backup unchanged.
- [x] 2.5 Assert public dry-run and real execution retain the same ordered immutable command snapshot with exactly one before/after restore probe pair and no source-specific third probe.

## 3. Verification

- [x] 3.1 Run the focused environment checkout and CLI/SDK command-boundary tests that cover explicit-source COPY execution, probe ordering, typed errors, journal/provenance, rollback, redaction, locks, and cleanup.
- [x] 3.2 Run repository lint, formatting, strict typing, the standard offline test suite, strict OpenSpec validation, and `git diff --check`; record every command and exit code for review.
