## Planning basis

- Task: MYL-283 / GitHub #95.
- Change: `keep-command-plan-steps-in-sync`.
- Source snapshot: approved `origin/main` base `3d688b26b463d273e80fa46500226158d7d9fab1`.
- Estimate source of truth: planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`.
- Confidence: medium. The shared constructor, private projection API, known detached mismatch, focused regression modules, and a representative invalid test double are inspectable; the bounded uncertainty is how many additional latent mismatches the repository-wide suite exposes.
- Calibration: evidence-based engineering estimate for one experienced developer familiar with the stack, without AI acceleration. No historical duration benchmark was available, and tests were not run during estimation.
- Assumptions: GitHub #95 remains limited to construction-time step parity and detached-launch plan completeness; existing observations, warnings, execution order, redaction, serialization, public signatures, and persistence schemas remain unchanged; repository tooling is available to the implementer.

The authoritative `Estimate, hours` property falls within the mandatory single-package threshold, so the implementation mode is `single_wp_no_dag`.

## WP-01 — Enforce one authoritative command step sequence

- Task coverage: `1.1`, `1.2`, `2.1`, `2.2`, `3.1`, `3.2`, `3.3`, `4.1`, and `4.2` from `tasks.md`, each covered exactly once.
- Deliverable: all commands reject any ordered public/private step-projection mismatch at the shared construction boundary, detached launch previews all captured lifecycle actions without side effects, and valid builders/tests use one captured sequence for both projections.
- Owned responsibility scope: command construction in `src/odoo_instance_sdk/execution.py`; detached command assembly in `src/odoo_instance_sdk/resources/instance/planning.py`; directly related execution-model and detached-run tests; invalid production builders or test doubles exposed by the invariant; directly related fixtures, documentation, snapshots, and formatting-only support files needed for repository checks. OpenSpec artifacts are authoritative context and are not an implementation write target except for apply-workflow task checkbox updates.
- Critical shared files: `src/odoo_instance_sdk/execution.py`, `src/odoo_instance_sdk/resources/instance/planning.py`, `tests/unit/test_execution_models.py`, and `tests/unit/test_run_detached.py`; any additional compatibility repair remains within this package to avoid split ownership of the central invariant.
- Contract surface: `Command.from_prepared()` and its `Command.create()` delegation path; `ExecutionPlan.steps`; `PreparedCommand.steps` and each prepared step's existing `public_projection()`; `PlanValidationError`; detached launch action identifiers and plan observations. Public signatures, JSON shapes, dependency graph, database/catalog schema, and runtime effect semantics remain unchanged.
- Definition of done / evidence: every covered task is checked; mismatch regressions prove missing, extra, reordered, differently typed, and field-different projections fail before command registration/run; a valid matching command retains fingerprint and repeatable-run behavior; detached dry-run contains all lifecycle and preparation/process projections while starting no process or action; focused tests, the complete automated suite, formatting/lint, strict type, production line-limit, and execution-architecture checks pass; the final diff contains no unrelated production change or new abstraction.
- Parallel-safety rationale: constructor validation, detached caller repair, downstream builder/test-double compatibility, and regression evidence all converge on one shared invariant. Splitting the work would create overlapping write zones and intermediate states in which the repository cannot construct known commands, so one atomic package is the only independently deliverable unit.

## Coverage proof

| Work package | OpenSpec tasks | Coverage |
| --- | --- | --- |
| `WP-01` | `1.1`, `1.2`, `2.1`, `2.2`, `3.1`, `3.2`, `3.3`, `4.1`, `4.2` | All tasks exactly once |
