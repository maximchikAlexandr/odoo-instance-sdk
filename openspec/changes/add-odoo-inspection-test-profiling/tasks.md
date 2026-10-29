## 1. Typed SDK contracts

- [ ] 1.1 Add frozen, unknown-field-forbidden model/field/XML-ID inspection result models with exact nullable fields, identifier/value validation, 256-code-point string bounds, and public lazy exports; add focused construction, invalid-value, serialization, and secret/callable exclusion tests.
- [ ] 1.2 Add frozen test profile entry/aggregate models, `OdooTestSpec.profile=false`, nullable `OdooTestResult.profile`, and public exports; test finite/non-negative validation, exact totals/entry types, and compatibility construction without profile.
- [ ] 1.3 Add the focused `OdooInspector` resource to `OdooInstance` initialization with only the three convenience methods and matching command siblings; prove each convenience method delegates once and no dynamic ORM/query surface is exported.

## 2. Read-only registry inspection

- [ ] 2.1 Implement fixed model/field/XML-ID shell sources and strict payload converters on the existing `_shell_script_command(commit=False, exclusive=False)` boundary, including JSON-safe identifier embedding, missing-object null shapes, bounded primitives, and no arbitrary record or callable fields.
- [ ] 2.2 Add recording/fake-executor tests for immutable plan/execution parity, configured database/cwd/environment reuse, rollback action, no HTTP prerequisite, one process only, early invalid-input rejection, sanitized failures, and no commit/write/raw-SQL behavior.
- [ ] 2.3 Add focused SDK result tests for existing/missing model, existing/missing field, existing/missing XML ID, relation/related/compute presence, long-string bounds, strict malformed payload rejection, and absence of credentials/arbitrary values.

## 3. Existing runner profile extension

- [ ] 3.1 Extend `_test_runner_source()` minimally so the existing single Odoo 19 `run_tests()` result emits native `_r.stats` primitives only when requested, while preserving the unprofiled payload and using no log parsing, second invocation, or custom counters.
- [ ] 3.2 Extend the existing test result converter to validate native stat IDs/durations/query counts, sort by duration descending then test ID, derive aggregate totals, and fail closed on requested missing/invalid stats without changing counts, failures, zero-tests, diagnostics, or exit rules.
- [ ] 3.3 Extend focused runner/model tests for profile flag propagation, one native invocation, stable ordering/totals, invalid/non-finite/negative values, success/failure/process/zero-test behavior, and the exact unprofiled result/payload compatibility shape.

## 4. CLI adapters and canonical output

- [ ] 4.1 Register one lazy `inspect` group and its three bounded leaves, using existing root context resolution and only matching public SDK primitives; add help/parser tests proving no command-local environment, dry-run, arbitrary ORM/SQL, field-value, or HTTP-server options.
- [ ] 4.2 Project each inspection result through the existing output document and a command-local Rich table; parameterize Rich/JSON/strict-TOON existing/missing parity, one clean machine document, no ANSI/progress/native logs, stderr-only sanitized failures, and secret/arbitrary/callable exclusion.
- [ ] 4.3 Add the three inspect rows exactly once to `PUBLIC_LEAF_CASES` with matching SDK primitive, bounded read-only classification, no dry-run, and focused real-Odoo disposition; update canonical leaf counts/generated command matrix through its existing generator and tests rather than adding a second table.
- [ ] 4.4 Add `--profile` to top-level `test` and compatibility `module test`, pass it into the same `OdooTestSpec`, conditionally add one typed profile projection only for executed profiled runs, and retain the existing runner, selection, dry-run plan, diagnostics, and exit paths.
- [ ] 4.5 Extend CLI matrices for explicit/cwd/changed selection, tags/reload/allow-empty, alias parity, stable Rich/JSON/TOON profile ordering, profile-aware dry-run, no-addon-change omission, failures, and exact absence of `profile` from every unprofiled decoded result.

## 5. Integration, documentation, and delivery verification

- [ ] 5.1 Extend the existing real-Odoo probe addon and focused real-Odoo tests to inspect one model, one field, one existing/missing XML ID, prove rollback/no mutation/no HTTP prerequisite/no sensitive field output, and assert one non-empty native Odoo 19 profile entry with non-negative duration/query count from the existing test runner. In the test repository, add `tests/e2e/fingerprint_database.py` and focused fake-process/config tests that bind the exact numbered environment/database, hash a normalized whole-database data dump without exposing connection/data values, emit only the bounded delivery-plan fields, and delete temporary dumps on every exit path; publish its functional MR/commit before Tester readiness.
- [ ] 5.2 Update README, CLI help, Python SDK documentation, and directly related documentation contracts with exact inspect/profile examples, context resolution, missing-object behavior, rollback-only/native-stat guarantees, unprofiled compatibility, and explicit non-goals.
- [ ] 5.3 Run focused unit/contract checks, lint/types, ordinary offline tests, existing real-Odoo CI dispositions, packaging/docs checks, `git diff --check`, and strict scoped OpenSpec validation on the exact candidate; keep each local automated suite within the workspace ten-minute rule and move only an over-budget suite to GitHub Actions on the published revision.
- [ ] 5.4 Publish one exact implementation candidate and preserve product/test repository branches and commits for independent Tester verification; Tester shall use the delivery plan's isolated numbered-slot procedure, attach required before/after CLI images and sanitized evidence to Multica with read-back, clean only its disposable slot/database/processes, and never edit product tests or merge/close/delete branches without separate authority.
