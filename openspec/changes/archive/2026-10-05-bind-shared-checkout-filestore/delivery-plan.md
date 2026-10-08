## Delivery Contract

- Task key: `MYL-398`
- OpenSpec change: `bind-shared-checkout-filestore`
- Approved base: `origin/main` at `c1e57b79f39e529a50c25818134c06309384ee23`
- Delivery mode: `single_wp_no_dag`
- Topology rule: the authoritative `Estimate, hours` property on the root planning issue does not exceed the governing threshold, so exactly one all-covering work package is permitted. The package has no dependency or stage metadata.
- Estimate source: the root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numeric totals are intentionally not duplicated here.
- Estimate basis: remaining active developer effort for one experienced developer familiar with Python, pytest, and this repository, without AI acceleration. Unattended CI, approval queues, meetings, and external blocking are excluded.
- Confidence: high. The missing argument and its single configuration-write path are directly inspectable, the canonical path and validator already exist, and public checkout fixtures cover both database modes. Uncertainty is concentrated in copy-fixture adaptation and cross-platform symlink setup.
- Calibration: evidence-based and uncalibrated; no comparable historical elapsed-time record was used. Tests were not run to manufacture timing evidence.

## WP-MYL-398-01 — Bind checkout to the managed project filestore

**Task coverage (complete and one-time):** `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `2.4` from `tasks.md`. No task is assigned outside this work package.

**Deliverable:** managed Compose checkout captures a verified canonical project filestore path in its immutable plan and writes it to generated configuration in both shared and copy modes; external source bindings remain unchanged; unsafe managed paths fail before durable environment state can become `READY`.

**Owned responsibility scope:**

- Checkout planning and its frozen private input in `src/odoo_instance_sdk/resources/environment/checkout.py` and `checkout_planning.py`.
- The existing configuration-write call in `src/odoo_instance_sdk/resources/environment/checkout_execution.py`, reusing helpers from `internal/project_init.py` and behavior from `internal/generated_config.py`.
- Directly related public-resource checkout/removal tests, fixtures, snapshots, and documentation needed to verify the accepted OpenSpec contract, primarily under `tests/unit/resources/`.
- No public API, CLI option, dependency, catalogue schema, project manifest format, process boundary, filestore migration, or ownership redesign unless a directly observed contract break requires the smallest compatible adjustment.

**Contract surface:**

- A managed Compose project is identified only by `ProjectConfig.postgres.mode == "compose"`; no heuristic based on source paths or database values is added.
- `project_owned_data_dir(repo_root)` and `verify_project_owned_data_dir(..., require_exists=False)` remain the sole canonical computation and safety proof.
- The verified optional path is captured once in `_CheckoutPlan`; execution consumes it without recomputation.
- Passing `None` to the existing generated-config path preserves explicit external `data_dir`; a captured Compose path overrides a missing or stale source binding.
- Shared environments retain non-ownership of the source database and filestore; checkout does not create, copy, migrate, delete, or adopt filestore contents.

**Definition of done / evidence:**

- Every OpenSpec task is checked only after implementation or verification evidence exists.
- Public resource tests prove managed shared binding from a legacy source config, managed copy binding, external explicit-binding preservation, and fail-before-catalogue behavior for an escaping symlink.
- Existing shared removal tests prove that source database and project filestore ownership remain unchanged.
- `uv run pytest -q tests/unit/resources/test_environment_checkout.py tests/unit/resources/test_environment_remove.py` passes.
- `make pr` passes, or any unavailable external prerequisite is recorded separately from code regressions.
- `openspec validate bind-shared-checkout-filestore --strict` remains successful and the implementation diff contains no unrelated refactor or new abstraction.

**Execution rationale:** planning, generation, lifecycle failure, and both database-mode tests share the same frozen plan and checkout fixtures. One package gives the Implementer/WP Verifier pair one atomic root-cause deliverable and avoids conflicting edits to the same checkout modules.

## Estimation Evidence and Assumptions

- Inspected source: `_prepare_checkout()`, frozen `_CheckoutPlan`, `do_checkout()`, `generate_config()`/`render_config()`, `project_owned_data_dir()`, `verify_project_owned_data_dir()`, project manifest parsing, and public checkout tests.
- The estimate includes implementation, fixture adaptation, focused and repository verification, and likely review repairs. It excludes deployment, live Odoo/PostgreSQL execution, data migration, and new behavior outside the accepted delta spec.
- The estimate becomes invalid if acceptance expands to migrate existing generated configs, support a separate external filestore ownership model, alter copy restore semantics, or change public plan/schema contracts.
