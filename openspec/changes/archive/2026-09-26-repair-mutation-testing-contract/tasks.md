## 1. Mutation runner contract

- [x] 1.1 Add a focused developer runner that creates `.artifacts/mutation/results.txt` before execution, records stage-labelled combined diagnostics, runs `mutmut results` only after a successful `mutmut run`, and returns the original failing child status.
- [x] 1.2 Add parametrized unit tests for runner success, empty child output, a distinctive non-zero child status, and result-command failure, asserting non-empty stage-labelled reports, no later-stage execution after failure, and exact exit-code preservation.
- [x] 1.3 Delegate the existing `make mutation` target to the tested runner without adding a second developer or CI entrypoint.

## 2. Import-complete bounded mutation configuration

- [x] 2.1 Change `pyproject.toml` so mutmut copies the full `src/odoo_instance_sdk` package through `source_paths` while `only_mutate` names exactly the five existing security/normalization files; retain the frozen `mutmut>=3,<4` dependency and current offline unit selection.
- [x] 2.2 Commit `tests/fixtures/mutation_smoke/pyproject.toml`, a passing test that imports `odoo_instance_sdk.cli` and exercises `validate_db_name`, and a separate test that raises during collection after the CLI import; add a stdlib-only temporary-workspace harness that copies the real package and launches installed mutmut against only `odoo_instance_sdk.internal.db_name.validate_db_name*`.
- [x] 2.3 Make the bounded harness assert, for the passing fixture, zero status, a non-empty report, and at least one terminal mutant classification; for the controlled collection-failure fixture, assert a real non-zero mutmut status and a separate non-empty failure report.
- [x] 2.4 Run the bounded real-mutmut regression on every `make mutation` before the full audit, add repository contract assertions for that permanent wiring, then record full-audit evidence that baseline collection succeeds, mutants from all five target files execute, and the final report contains mutmut result classifications.

## 3. Truthful scheduled and manual workflow

- [x] 3.1 Remove mutation-job failure masking, keep artifact upload under `if: always()`, and set missing `mutation-results` input to an explicit upload error for both existing triggers.
- [x] 3.2 Extend `tests/unit/test_ci_contract.py` with semantic assertions for the shared `make mutation` command, absence of `continue-on-error`, always-run upload, exact artifact path/name, and fail-on-missing behavior.

## 4. Documentation and verification

- [x] 4.1 Update `CONTRIBUTING.md` to document frozen dependency setup, exact targeted scope, diagnostic/non-required policy, honest failure status, and the report retained on both success and failure; align README only if its Development summary otherwise conflicts.
- [x] 4.2 Run focused mutation-runner/config/workflow/documentation tests, repository lint/type checks for changed Python, strict OpenSpec validation, and confirm production/public API files remain unchanged.
