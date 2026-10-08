## 1. Capture the backend attribution plan

- [x] 1.1 Replace the execution-time credential resolver construction with one deterministic command-construction capture of selected catalogue database credentials, preserving project filtering, database deduplication, and typed missing-credential degradation.
- [x] 1.2 Capture `snapshot_command()` once and build one canonical `PsqlSpecification` for every eligible database using the existing query, timeout, maintenance database, stable step id, private environment, redacted public projection, and `_require_binary=False` so unresolved `psql` yields an inspectable literal-executable step instead of construction-time `FileNotFoundError`.
- [x] 1.3 Add the captured PostgreSQL steps to the exact outer prepared tuple and public `processes_command()` plan, and inject a database-keyed runner that consumes only those specifications through `execute_psql()` at most once while explicitly skipping unused planned steps.

## 2. Preserve bounded degradation

- [x] 2.1 Remove late dynamic `run_psql()` construction from the active process-inventory context so an absent captured specification performs no launch and returns the existing typed unavailable backend result.
- [x] 2.2 In the captured backend runner catch only concrete `ProcessSpawnError`, `ProcessTimeoutError`, and bounded `ProcessOutputLimitError` outcomes, mapping them to `psql_missing`, `timeout`, and `query_failed`; classify completed non-zero and malformed successful payloads through the existing inventory seam, and allow `UnplannedStepError` and `DuplicateStepError` to propagate unchanged.
- [x] 2.3 Verify that successful attribution retains ownership, connection identity, VM/host PID scope, and single-snapshot semantics and that every failed attribution leaves unrelated Odoo, environment, shared-resource, and storage facts intact.

## 3. Prove the public boundary and repository gates

- [x] 3.1 Add a public `EnvironmentMonitor.processes_command()` recording-executor regression for one healthy owned cluster/database that asserts the exact planned `pg_stat_activity` step, secret-free public projection, one consumption, and a successful `ProcessInventory` without ledger exceptions.
- [x] 3.2 Add paired timeout/non-zero and invalid-response cases plus a public-boundary regression where resolver absence still constructs an inspectable literal-`psql` step and executor `ProcessSpawnError` yields backend-only `psql_missing`; assert successful remaining inventory, no duplicate launch, and propagation of ledger exceptions.
- [x] 3.3 Extend the existing SDK/CLI output-boundary coverage to prove Rich, JSON, and TOON reuse one command result without an extra snapshot or backend sample.
- [x] 3.4 Run the focused process-inventory and CLI tests, process-boundary/architecture tests, Ruff formatting and lint, strict mypy, `git diff --check`, and strict OpenSpec validation; record all command results for review.
