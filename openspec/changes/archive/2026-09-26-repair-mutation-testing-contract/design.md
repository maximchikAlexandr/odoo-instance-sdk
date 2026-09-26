## Context

GitHub issue #99 documents a false-green scheduled workflow at base `3d688b26b463d273e80fa46500226158d7d9fab1`. `pyproject.toml` currently gives mutmut five file-level `source_paths`; mutmut builds a `mutants/` source tree from that input, while the selected `tests/unit` suite imports modules outside those five files, including `odoo_instance_sdk.cli`. Baseline collection therefore stops with `ModuleNotFoundError` before any mutant is tested. `Makefile` writes `results.txt` only after a successful `mutmut run`, and `.github/workflows/mutation.yml` combines `continue-on-error: true` with an always-run upload, producing a green workflow with no artifact.

The frozen lock resolves `mutmut 3.7.0`. The supported 3.6+ configuration separates the importable source tree (`source_paths`) from the mutation filter (`only_mutate`), so the existing framework can represent the required boundary directly. The change crosses tool configuration, command orchestration, workflow semantics, tests, and contributor documentation, but does not touch production code or public API.

## Goals / Non-Goals

**Goals:**

- make the copied mutation workspace import-complete while preserving the exact five-file target set;
- make one `make mutation` entrypoint produce a useful report on success and failure and preserve non-zero failures;
- make scheduled/manual GitHub Actions status reflect the actual command result;
- add a bounded committed mutmut integration regression plus deterministic orchestration contract tests and retain the full mutation run as end-to-end evidence;
- document diagnostic/non-required policy separately from execution success.

**Non-Goals:**

- adding mutation testing to PR-required gates or `make pr`;
- broadening mutation targets, changing survivor policy, or requiring 100% mutation score;
- replacing mutmut, changing production Python/CLI behavior, or running real Odoo;
- encoding GitHub branch-protection settings in repository files.

## Decisions

### 1. Separate import roots from mutation targets with native mutmut configuration

Set `source_paths` to the package directory `src/odoo_instance_sdk` so mutmut copies the complete import graph. Set `only_mutate` to the five existing security/normalization paths. Keep the frozen `mutmut>=3,<4` group and current unit-test selection.

This uses the capability present in locked mutmut 3.7.0 and fixes the actual boundary: tests need the full package, while the audit needs a narrow target set. Listing more files in `also_copy` was rejected because it duplicates package topology and can drift whenever imports change. Expanding `source_paths` without `only_mutate` was rejected because it would silently turn the full package into mutation scope.

### 2. Put failure-safe report orchestration in a small Python script

Add a focused developer script that creates/truncates the report, records stage headers, invokes `mutmut run`, invokes `mutmut results` only after a successful run, streams/captures diagnostics into the same report, and returns the failing child status. `make mutation` remains the public developer entrypoint and delegates to this script through `uv run python`.

A Python orchestration seam is chosen over a compound POSIX shell recipe because exit-code preservation, dual stdout/stderr capture, deterministic stage labelling, and unit tests are materially clearer without shell-specific `pipefail` behavior. The script is developer tooling, not production runtime, and shall accept injectable command arguments/process execution at its internal test boundary without creating a general command-runner abstraction.

### 3. Treat the report as a required output on every attempted audit

The runner creates `.artifacts/mutation/results.txt` before the first child process and appends all stage output. On successful mutation execution it appends `mutmut results`; on any child failure it appends an explicit failure marker and exits with that child status. Empty output is still converted into a non-empty stage-labelled diagnostic.

This keeps the report useful for pre-mutant failures without converting failure into success. Synthesizing a successful category summary after a failed run is prohibited because it would recreate the false-green ambiguity.

### 4. Make workflow failure semantics explicit

Remove job-level `continue-on-error`. Keep artifact upload under `if: always()` so failure diagnostics survive, and set `if-no-files-found: error` so a regression in the report contract is itself a visible failure. Both existing triggers continue to execute the same job and Make target.

The workflow remains non-required by branch-protection policy; repository YAML shall not weaken job conclusions to model that policy. A separate PR mutation job is rejected because it duplicates the costly audit and contradicts the original diagnostic scope.

### 5. Run a real bounded mutmut regression before every full audit

Commit `tests/fixtures/mutation_smoke/pyproject.toml`, a passing fixture test, and a collection-failure fixture. Add `scripts/check_mutation_integration.py`, called by the report-first runner before the full audit. The script SHALL create a temporary workspace, copy the real `src/odoo_instance_sdk` package into it, and use the committed fixture configuration with full-package `source_paths` and `internal/db_name.py` as its sole `only_mutate` file.

The success case SHALL copy the passing fixture as the only selected test. That test SHALL import `odoo_instance_sdk.cli`, import and exercise `validate_db_name`, and expose no repository `PYTHONPATH`. The script SHALL invoke the installed mutmut process with the stable filter `odoo_instance_sdk.internal.db_name.validate_db_name*`, run `mutmut results`, and fail unless the captured non-empty report proves that at least one mutant received a terminal `killed`, `survived`, `timeout`, or `suspicious` classification.

The failure case SHALL repeat the isolated run with the committed collection-failure fixture installed as the only selected test. That fixture SHALL raise during pytest collection after importing `odoo_instance_sdk.cli`. The script SHALL require a real non-zero mutmut status and a non-empty collection-failure report. Separately, runner unit tests SHALL feed a distinctive non-zero child status and assert that the runner returns that exact status and retains the stage-labelled report. Together these checks prove actual mutmut integration failure and exact orchestration propagation without relying on a mock for the integration boundary.

The bounded integration script runs on every `make mutation`, so scheduled and manual CI cannot regress the import-copy contract unnoticed. The subsequent full audit remains the evidence that every configured target file produces executed mutants and final classifications. Existing CI-contract tests continue to cover configuration and workflow YAML invariants.

A mock-only test was rejected because it cannot establish that mutmut loads the copied package. Running the entire five-file audit inside ordinary pytest was rejected because it duplicates the scheduled workload and installs mutation dependencies into the normal test group. The one-function temporary-workspace smoke is the smallest permanent check that exercises the real boundary.

### 6. Keep documentation bounded to contributor behavior

Update `CONTRIBUTING.md` with the dependency sync command, exact five-file targeted nature, failure/report semantics, and distinction between diagnostic/non-required policy and honest job status. Update README only if its existing Development summary makes an inconsistent claim after implementation; no user-facing product documentation is otherwise needed.

## Risks / Trade-offs

- **[mutmut minor-version semantics change inside `<4`]** → contract tests pin the exact configuration shape and the frozen lock; implementation verifies against the locked 3.7.0 behavior before publication.
- **[Full package copy increases setup time]** → mutation generation stays filtered to five small modules, and the 30-minute workflow budget remains the measured guardrail.
- **[Permanent smoke adds duplicate mutation work]** → it selects only `validate_db_name*` in one target file and one test, while the later full audit remains authoritative for all five targets.
- **[A child is terminated before buffered diagnostics flush]** → the runner writes stage headers before spawn, captures both streams, and guarantees a non-empty report even when child output is empty.
- **[Survivors make `mutmut results` informational rather than green-quality proof]** → workflow success means the audit executed and reported; survivor policy remains explicitly out of scope.
- **[YAML text tests become brittle]** → extend the existing `tests/unit/test_ci_contract.py` style with semantic YAML/repository assertions limited to behaviorally important keys.

## Migration Plan

1. Add the report-first runner and deterministic unit tests that prove exact child-status propagation.
2. Add the committed passing and collection-failure fixtures plus the temporary-workspace integration script; make the script launch real mutmut for `validate_db_name*` and validate both reports.
3. Change production mutmut configuration to full-package `source_paths` plus exact five-file `only_mutate` targets; add configuration contract coverage.
4. Delegate `make mutation` to the runner, with the bounded integration regression before the full audit.
5. Remove workflow masking, require the artifact path, and update CI contract tests.
6. Update contributor documentation and run focused repository checks.
7. Run the frozen-group `make mutation` as implementation evidence, recording the bounded real-mutmut regression, successful full baseline collection, mutants from all five files, and a non-empty final report.

Rollback is one commit revert: no data, API, schema, or migration state is created. Existing ignored `mutants/` and `.artifacts/` outputs remain disposable.

## Open Questions

None. Survivor acceptance thresholds and future PR-gate promotion require a separate change after a stable baseline exists.
