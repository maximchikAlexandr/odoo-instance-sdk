## 1. Mutation runner contract

- [ ] 1.1 Add a focused developer runner that creates `.artifacts/mutation/results.txt` before execution, records stage-labelled combined diagnostics, runs `mutmut results` only after a successful `mutmut run`, and returns the original failing child status.
- [ ] 1.2 Add parametrized unit tests for runner success, empty child output, controlled collection failure, and result-command failure, asserting non-empty reports and exact exit-code preservation.
- [ ] 1.3 Delegate the existing `make mutation` target to the tested runner without adding a second developer or CI entrypoint.

## 2. Import-complete bounded mutation configuration

- [ ] 2.1 Change `pyproject.toml` so mutmut copies the full `src/odoo_instance_sdk` package through `source_paths` while `only_mutate` names exactly the five existing security/normalization files; retain the frozen `mutmut>=3,<4` dependency and current offline unit selection.
- [ ] 2.2 Add repository contract tests that parse the mutation configuration, assert the exact target set, and exercise a representative selected test importing `odoo_instance_sdk.cli` from the mutation workspace.
- [ ] 2.3 Run the locked mutation audit and record evidence that baseline collection succeeds, mutants from all five target files execute, and the final report contains mutmut result classifications.

## 3. Truthful scheduled and manual workflow

- [ ] 3.1 Remove mutation-job failure masking, keep artifact upload under `if: always()`, and set missing `mutation-results` input to an explicit upload error for both existing triggers.
- [ ] 3.2 Extend `tests/unit/test_ci_contract.py` with semantic assertions for the shared `make mutation` command, absence of `continue-on-error`, always-run upload, exact artifact path/name, and fail-on-missing behavior.

## 4. Documentation and verification

- [ ] 4.1 Update `CONTRIBUTING.md` to document frozen dependency setup, exact targeted scope, diagnostic/non-required policy, honest failure status, and the report retained on both success and failure; align README only if its Development summary otherwise conflicts.
- [ ] 4.2 Run focused mutation-runner/config/workflow/documentation tests, repository lint/type checks for changed Python, strict OpenSpec validation, and confirm production/public API files remain unchanged.
