## Planning basis

- Task: MYL-243 / GitHub #82.
- Change: `fix-checkout-lock-python-target`.
- Source snapshot: approved `origin/main` base recorded by the planning handoff.
- Estimate source of truth: planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`.
- Confidence: high. The implementation path, both Python ownership modes, fake process harness, and focused regression location are present in the inspected snapshot; no public API, schema, dependency, migration, or external integration work is required.
- Calibration: evidence-based engineering estimate for one experienced developer familiar with the stack, without AI acceleration; no historical duration benchmark was available, and tests were not run during estimation.
- Assumptions: GitHub #82 remains limited to checkout lock compilation; `env sync` behavior and hash-lock semantics remain unchanged; existing repository tooling is available to the implementer.

The authoritative `Estimate, hours` property falls within the mandatory single-package threshold, so the implementation mode is `single_wp_no_dag`.

## WP-01 — Align checkout compilation with the target Python

- Task coverage: `1.1`, `1.2`, `2.1`, `2.2`, and `3.1` from `tasks.md`, each covered exactly once.
- Deliverable: checkout's immutable legacy dependency plan passes one resolved Python executable to both `uv pip compile` and the following mode-specific install/sync step, with reused and owned regression coverage and repository verification evidence.
- Owned responsibility scope: the checkout dependency-step construction in `src/odoo_instance_sdk/resources/environment/checkout_artifacts.py`; directly related unit tests, fixtures, and test helpers in `tests/unit/resources/test_environment_python.py`; any formatting-only changes required by repository checks. The OpenSpec package is authoritative context, not an implementation write target except for task checkbox updates made by the apply workflow.
- Critical shared files: `checkout_artifacts.py` and `test_environment_python.py`; all related implementation and verification edits stay in this package to avoid conflicting ownership.
- Contract surface: the `development-environment` delta requirement for checkout compile target equality; existing immutable command/process projection; reused `pip install`, owned `pip sync`, and hash-lock compile bypass behavior. Public SDK/CLI signatures and catalog schema remain unchanged.
- Definition of done / evidence: every covered task is checked; a parameterized test proves the compile and install/sync `--python` values are equal in reused and owned modes; existing hash-lock bypass coverage passes; focused environment-Python tests, formatting/lint, and strict type checks pass; the final diff contains no unrelated production change or new abstraction.
- Parallel-safety rationale: all work converges on one small command-construction branch and its shared test module, so splitting ownership would create overlapping write zones without an independent deliverable.

## Coverage proof

| Work package | OpenSpec tasks | Coverage |
| --- | --- | --- |
| `WP-01` | `1.1`, `1.2`, `2.1`, `2.2`, `3.1` | All tasks exactly once |
