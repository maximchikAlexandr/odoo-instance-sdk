## Purpose

Define an honest, reproducible targeted mutation-testing contract whose import-complete workspace, fail-closed execution, retained diagnostics, CI behavior, regression proof, and contributor guidance remain aligned.

## Requirements

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

The `Mutation Testing` GitHub Actions workflow SHALL derive its target matrix from the exact `[tool.mutmut].only_mutate` list used by `make mutation`, SHALL invoke the same target-aware `make mutation` contract for both `schedule` and `workflow_dispatch`, and SHALL run one target per matrix shard with `fail-fast` disabled. A failed dependency bootstrap, target validation, test collection, mutation execution, result collection, completeness check, required artifact upload, aggregate validation, or baseline comparison SHALL make the workflow fail; diagnostic/non-required status SHALL be expressed only by repository required-check policy.

#### Scenario: Scheduled or manual audit succeeds

- **WHEN** either supported trigger completes every derived target shard successfully within its configured timeout
- **THEN** every target SHALL have exactly one non-empty shard artifact containing its terminal result classification
- **THEN** the always-run aggregate job SHALL upload one non-empty summary containing per-target and total `killed`, `survived`, `timeout`, `suspicious`, and `not checked` counts
- **THEN** total `not checked` SHALL equal zero and the workflow SHALL report success

#### Scenario: Mutation execution fails

- **WHEN** target-aware `make mutation` exits non-zero in any shard under either supported trigger
- **THEN** that shard's artifact upload SHALL still run and preserve the non-empty diagnostic report
- **THEN** remaining shards SHALL continue instead of being cancelled by matrix fail-fast
- **THEN** the aggregate job SHALL still inspect all available artifacts and the workflow SHALL report failure

#### Scenario: Required report is missing

- **WHEN** any shard upload cannot find its required report or aggregation cannot find exactly one report for every canonical target
- **THEN** artifact publication or aggregation SHALL fail explicitly rather than emit a warning-only successful workflow

#### Scenario: Shard exceeds the configured budget

- **WHEN** any target shard fails to reach terminal classification within its configured job timeout
- **THEN** that shard and the overall workflow SHALL fail
- **THEN** an incomplete aggregate SHALL NOT be accepted as the mutation baseline or successful audit evidence

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

Contributor documentation SHALL identify `make mutation` as a scheduled/manual diagnostic that is not a required PR gate, while stating that the command itself fails on infrastructure, test, target-validation, incomplete-classification, aggregation, or baseline-regression errors. It SHALL document the frozen `mutation` and `test` groups, the unchanged five-file scope, optional single-target invocation, per-shard diagnostic artifacts, the aggregate summary, and the review-only baseline update rule.

#### Scenario: Contributor prepares a local mutation audit

- **WHEN** a contributor follows the documented setup and mutation instructions
- **THEN** the documented dependency command, entrypoint, full and single-target scope, artifact paths, completeness checks, and failure semantics SHALL match the committed configuration, Make target, runner, helper, baseline, and workflow

#### Scenario: Contributor improves the baseline

- **WHEN** a complete accepted audit reduces survived or timeout counts
- **THEN** documentation SHALL direct the contributor to lower the version-controlled baseline in a separate reviewable change backed by the complete summary
- **THEN** no workflow SHALL rewrite or relax the baseline automatically

### Requirement: Exact-once bounded mutation partition

The repository SHALL derive a matrix from the canonical `[tool.mutmut].only_mutate` configuration without maintaining another target list. Every configured target SHALL appear in exactly one shard with a stable filesystem-safe identifier. A shard SHALL accept only its exact configured target, apply the corresponding mutmut filter to both execution and result collection, and SHALL NOT execute or report mutants belonging to another target.

#### Scenario: Canonical targets are partitioned

- **WHEN** the workflow prepare step reads the five configured mutation targets
- **THEN** it SHALL produce five unique matrix entries whose target set equals the configured target set
- **THEN** every target SHALL be assigned once with no missing, duplicate, or unknown entry

#### Scenario: Unknown target is requested

- **WHEN** `make mutation` receives a target that is absent from `[tool.mutmut].only_mutate` or resolves ambiguously
- **THEN** the runner SHALL fail before launching the bounded smoke or mutmut
- **THEN** it SHALL retain a non-empty target-validation diagnostic

#### Scenario: Target shard completes

- **WHEN** a shard executes its valid target
- **THEN** the runner SHALL apply the same target filter to `mutmut run` and `mutmut results`
- **THEN** its report SHALL contain a positive total and numeric `killed`, `survived`, `timeout`, `suspicious`, and `not checked` counts whose sum equals total
- **THEN** the shard SHALL succeed only when `not checked` equals zero

### Requirement: Complete aggregate mutation report

An always-run aggregate step SHALL validate the downloaded shard reports against the canonical target matrix and SHALL publish one deterministic summary containing every target exactly once plus summed `total`, `killed`, `survived`, `timeout`, `suspicious`, and `not checked` counts. Unknown, duplicate, missing, malformed, arithmetically inconsistent, empty, or incomplete reports SHALL make aggregation fail non-zero.

#### Scenario: All shard reports are complete

- **WHEN** exactly one valid terminal report exists for every canonical target
- **THEN** aggregation SHALL sum each category once, write a non-empty deterministic summary, and return success
- **THEN** the aggregate total SHALL equal the sum of terminal categories and total `not checked` SHALL equal zero

#### Scenario: Shard coverage is incomplete

- **WHEN** a configured target report is missing, repeated, empty, malformed, or names an unknown target
- **THEN** aggregation SHALL return non-zero and identify the invalid shard coverage
- **THEN** it SHALL NOT publish that result as a complete successful audit

#### Scenario: Classification is incomplete

- **WHEN** any shard reports non-zero `not checked` or category counts that do not reconcile with total
- **THEN** aggregation SHALL fail non-zero even if all expected artifact names exist

### Requirement: Version-controlled mutation regression baseline

The repository SHALL contain one reviewable baseline tied to its source evidence and containing aggregate `survived` and `timeout` counts from the complete current-main audit. Aggregation SHALL compare the complete current totals with that baseline and SHALL fail if either count increases. The workflow SHALL NOT create, raise, or rewrite the baseline automatically; `not checked` SHALL remain a strict zero-completeness condition rather than a baselined value.

#### Scenario: Audit stays within baseline

- **WHEN** a complete aggregate has survived and timeout counts less than or equal to the committed baseline
- **THEN** baseline validation SHALL succeed and the summary SHALL show both current and baseline counts

#### Scenario: Survivors or timeouts regress

- **WHEN** a complete aggregate has survived or timeout count greater than the committed baseline
- **THEN** baseline validation SHALL fail non-zero and identify each increased category
- **THEN** all shard and aggregate diagnostics SHALL remain available

#### Scenario: Baseline improves

- **WHEN** accepted complete evidence lowers survived or timeout counts
- **THEN** a separate reviewed repository change SHALL lower the corresponding baseline value and bind it to that evidence
- **THEN** the workflow SHALL NOT modify the baseline during the run
