## 1. Native Result Normalization

- [x] 1.1 Update the existing generated runner source to prefer Odoo 19 `failures_count` and `errors_count` while preserving supported legacy integer/list fallbacks and the single native `run_tests()` invocation.
- [x] 1.2 Add a parameterized generated-source regression matrix covering Odoo 19 failure, error, mixed, pass-only, field-precedence, and legacy fallback report shapes without weakening skipped or zero-test behavior.

## 2. Public Boundary and CI Regression

- [x] 2.1 Extend public SDK/CLI coverage to assert JSON counts, `failures`, diagnostics placement, and actual exit status for native failure/error results, and confirm `module test` remains equivalent to top-level `test` for the same selection.
- [x] 2.2 Extend the existing `odcli_e2e_probe` fixture and focused real-Odoo acceptance case with explicitly tagged deterministic PASS, FAIL, and ERROR methods; assert failure-only, error-only, mixed, and pass-only outcomes through a real Odoo 19 runner without changing the ordinary passing probe path.
- [x] 2.3 Update the existing E2E inventory/CI disposition only where needed so the focused Odoo 19 regression runs in the established real-Odoo job; add no new CI framework or private dependency.

## 3. Verification and Documentation

- [x] 3.1 Run focused unit/CLI checks, repository lint/type checks applicable to the touched Python paths, strict OpenSpec validation, and the established CI jobs on the exact published revision when a local automated suite would exceed ten minutes.
- [ ] 3.2 Complete the independent numbered-slot Odoo 19 walkthrough from `delivery-plan.md`, publish exact revision/command/exit/count evidence and required before/after text plus PNG attachments in Multica, and clean up the slot while retaining the evidence branch/MR/commits.
