## ADDED Requirements

### Requirement: Import-complete targeted mutation scope

Mutation configuration SHALL expose the complete `src/odoo_instance_sdk` package tree to baseline and mutant test processes while restricting generated mutation targets to exactly these existing files:

- `src/odoo_instance_sdk/internal/redact.py`
- `src/odoo_instance_sdk/internal/sanitize.py`
- `src/odoo_instance_sdk/internal/db_name.py`
- `src/odoo_instance_sdk/internal/urls.py`
- `src/odoo_instance_sdk/internal/address.py`

The configuration SHALL use the installed `mutmut>=3,<4` toolchain and SHALL NOT introduce a second mutation framework or make any other package module a mutation target.

#### Scenario: Full package imports remain available

- **WHEN** a clean checkout installs the frozen `mutation` and `test` dependency groups and starts `make mutation`
- **THEN** baseline collection SHALL import `odoo_instance_sdk.cli` and its package dependencies from the copied mutation tree without `ModuleNotFoundError`
- **THEN** mutation execution SHALL run at least one generated mutant belonging to each configured target file

#### Scenario: Target set stays bounded

- **WHEN** the mutation runner discovers Python modules below `src/odoo_instance_sdk`
- **THEN** it SHALL generate mutants only for the five explicitly listed target files
- **THEN** availability of the remaining package files for imports SHALL NOT make them mutation targets

### Requirement: Single honest mutation command

The repository SHALL provide `make mutation` as the single local, scheduled, and manual mutation entrypoint. The command SHALL create `.artifacts/mutation/results.txt` before invoking mutmut, SHALL preserve a non-zero exit from bootstrap, collection, execution, or result collection, and SHALL leave a non-empty diagnostic report for both success and failure.

#### Scenario: Successful mutation audit

- **WHEN** baseline collection and all selected mutant executions complete
- **THEN** `make mutation` SHALL exit successfully
- **THEN** `.artifacts/mutation/results.txt` SHALL be non-empty and SHALL contain the final mutmut result classification, including the available `killed`, `survived`, `timeout`, and `suspicious` categories

#### Scenario: Collection fails before any mutant runs

- **WHEN** a controlled test-collection error occurs during `make mutation`
- **THEN** the command SHALL exit non-zero with the original mutation-run failure semantics
- **THEN** `.artifacts/mutation/results.txt` SHALL remain non-empty and SHALL identify the failed stage and captured mutmut diagnostics

#### Scenario: Result collection fails

- **WHEN** mutant execution finishes but the final result command fails
- **THEN** `make mutation` SHALL exit non-zero
- **THEN** the report SHALL retain diagnostics from both the completed run and the failed result-collection stage

### Requirement: Truthful diagnostic workflow

The `Mutation Testing` GitHub Actions workflow SHALL invoke the same `make mutation` contract for both `schedule` and `workflow_dispatch`. A failed dependency bootstrap, test collection, mutation execution, result collection, or required artifact upload SHALL make the job and workflow fail; diagnostic/non-required status SHALL be expressed only by repository required-check policy.

#### Scenario: Scheduled or manual audit succeeds

- **WHEN** either supported trigger completes `make mutation` successfully
- **THEN** the workflow SHALL upload a non-empty artifact named `mutation-results` containing `results.txt`
- **THEN** the job and workflow SHALL report success

#### Scenario: Mutation execution fails

- **WHEN** `make mutation` exits non-zero under either supported trigger
- **THEN** the artifact upload SHALL still run and preserve the non-empty diagnostic report
- **THEN** the mutation job and workflow SHALL report failure rather than success

#### Scenario: Required report is missing

- **WHEN** the upload step cannot find `.artifacts/mutation/results.txt`
- **THEN** artifact publication SHALL fail explicitly rather than emit a warning-only successful workflow

### Requirement: Mutation contract regression proof

Repository verification SHALL run a committed bounded integration fixture with the real installed mutmut process before every full `make mutation` audit. The fixture SHALL use a temporary workspace containing the real copied `src/odoo_instance_sdk` package, SHALL restrict `only_mutate` to `src/odoo_instance_sdk/internal/db_name.py`, and SHALL invoke the stable mutant filter `odoo_instance_sdk.internal.db_name.validate_db_name*`. Deterministic runner tests SHALL separately prove exact exit-code propagation, while repository contract tests SHALL prove configuration and workflow invariants.

#### Scenario: Real bounded mutant execution is verified

- **WHEN** the committed passing fixture is executed from a clean temporary workspace during `make mutation`
- **THEN** its only selected test SHALL import `odoo_instance_sdk.cli`, import and exercise `validate_db_name`, and run without repository `PYTHONPATH`
- **THEN** the harness SHALL launch the installed mutmut process for `odoo_instance_sdk.internal.db_name.validate_db_name*`
- **THEN** verification SHALL assert zero status, a non-empty report, and at least one mutant classified as `killed`, `survived`, `timeout`, or `suspicious`

#### Scenario: Real controlled collection failure is verified

- **WHEN** the same bounded harness selects the committed fixture that raises during pytest collection after importing `odoo_instance_sdk.cli`
- **THEN** it SHALL launch the installed mutmut process and observe a non-zero collection status
- **THEN** verification SHALL assert a non-empty collection-failure report and SHALL treat the expected child failure as proof only after both assertions pass

#### Scenario: Exact child status is preserved

- **WHEN** the deterministic runner test supplies a distinctive non-zero status for the mutation stage
- **THEN** the runner SHALL return that exact status and retain a non-empty stage-labelled report without invoking later stages

#### Scenario: Repository contracts are verified

- **WHEN** CI contract tests inspect the committed mutation configuration and workflow
- **THEN** they SHALL assert full-package source availability, the exact five-file target set, permanent bounded integration invocation, absence of failure masking, always-run artifact upload, and fail-on-missing artifact behavior

### Requirement: Mutation developer documentation

Contributor documentation SHALL identify `make mutation` as a scheduled/manual diagnostic that is not a required PR gate, while stating that the command itself fails on infrastructure or test errors, requires the frozen `mutation` and `test` groups, preserves a diagnostic artifact on failure, and does not mutate the full package.

#### Scenario: Contributor prepares a local mutation audit

- **WHEN** a contributor follows the documented setup and mutation instructions
- **THEN** the documented dependency command, entrypoint, target-scope statement, artifact path, and failure semantics SHALL match the committed configuration, Make target, and workflow
