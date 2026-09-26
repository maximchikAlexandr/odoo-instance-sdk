## Planning Basis

- Planning issue: `MYL-280`
- OpenSpec change: `repair-mutation-testing-contract`
- Source baseline: `3d688b26b463d273e80fa46500226158d7d9fab1`
- Estimate source of truth: the planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`
- Confidence: medium; the affected configuration, workflow, Make target, contributor guide, and existing CI-contract tests are directly inspectable, while full-audit runtime and mutmut import-copy edge cases remain bounded implementation risks
- Calibration: uncalibrated for a single experienced developer familiar with the Python/pytest/GitHub Actions stack; no comparable historical delivery timings were available
- Topology mode: `single_wp_no_dag`, required by the authoritative estimate property

## Work Package

### WP-01 — Restore honest targeted mutation testing

**OpenSpec task coverage:** 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 4.1, 4.2. Each task is owned exactly once by this package.

**Deliverable:** A single reproducible `make mutation` contract that exposes the full package for imports, mutates only the five agreed security/normalization files, always leaves a useful report, preserves failure status, drives truthful scheduled/manual workflow conclusions, and is protected by a permanent bounded real-mutmut regression, focused orchestration tests, and matching contributor documentation.

**Owned responsibility scope:**

- mutation configuration and frozen dependency interpretation in `pyproject.toml` and `uv.lock` if lock refresh is actually required;
- the `Makefile` mutation target and one focused developer runner under `scripts/`;
- runner/config/import/workflow regression coverage under `tests/unit/`, the committed `tests/fixtures/mutation_smoke/` real-mutmut fixture, and its stdlib-only temporary-workspace harness;
- `.github/workflows/mutation.yml` upload and failure semantics;
- `CONTRIBUTING.md`, with `README.md` changed only if needed to remove a contradiction;
- directly related test fixtures, snapshots, evidence notes, and generated service files that are necessary to verify these responsibilities.

Production modules under `src/odoo_instance_sdk/`, public Python/CLI contracts, PR-required gate composition, broad mutation scope, survivor thresholds, and live-Odoo behavior are outside this package.

**Contract surface:**

- `make mutation` remains the sole local and CI entrypoint;
- `.artifacts/mutation/results.txt` exists and is non-empty after every attempted audit;
- child-process failures remain non-zero and stage-labelled in the report;
- mutmut receives full-package `source_paths` and the exact five-file `only_mutate` filter;
- every `make mutation` first runs a real mutmut smoke for `validate_db_name*` in an isolated copied package, proves a CLI-importing test executes at least one classified mutant, and separately proves controlled collection failure produces non-zero status and a non-empty report;
- scheduled and manual triggers use identical job behavior;
- `mutation-results` uploads even after execution failure, while a missing report is an upload error;
- diagnostic/non-required policy does not weaken the workflow conclusion.

**Definition of done and evidence:**

- every checkbox in `tasks.md` is completed with no product-scope expansion;
- focused runner tests cover success, empty output, a distinctive non-zero child status, and result failure with exact status/report assertions;
- the committed bounded integration harness launches the installed mutmut process in temporary workspaces, proves a CLI-importing passing fixture executes at least one `validate_db_name` mutant with a non-empty classified report, and proves a separate collection-failure fixture returns non-zero with a non-empty failure report;
- repository contract tests prove configuration scope, permanent smoke wiring, and workflow semantics;
- the locked real mutation audit completes baseline collection, executes mutants from every configured target, and produces final mutmut classifications in the report;
- changed Python passes focused lint and typing; affected unit/documentation/CI contract tests pass;
- strict OpenSpec validation and repository planning checks pass before implementation handoff evidence is accepted.

**Parallel-safety rationale:** The work intentionally remains indivisible. Configuration, runner exit semantics, workflow conclusions, tests, and documentation describe one observable contract and share critical files; splitting them would create transient false-green states and overlapping write zones without an independent deliverable.
