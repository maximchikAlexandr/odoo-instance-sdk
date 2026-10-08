## 1. Capture the managed filestore binding

- [x] 1.1 Add an optional private `data_dir` input to `_CheckoutPlan` and resolve it in `_prepare_checkout()` only for `ProjectConfig.postgres.mode == "compose"` by calling `project_owned_data_dir(repo_root)` and `verify_project_owned_data_dir(..., require_exists=False)` before any catalogue or filesystem mutation.
- [x] 1.2 Pass the captured `plan.data_dir` to the existing `generate_config(...)` call in checkout execution so managed Compose shared and copy configurations override `data_dir`, while a `None` value preserves external source configuration behavior.

## 2. Prove public checkout behavior

- [x] 2.1 Extend `tests/unit/resources/test_environment_checkout.py` with public shared-checkout regressions proving that a legacy managed Compose source without `data_dir` writes the canonical project-owned binding and reaches `READY`, while a non-Compose source with explicit `data_dir` remains unchanged.
- [x] 2.2 Extend the existing public copy-checkout coverage to assert the same managed Compose `data_dir` binding without adding a second copy harness.
- [x] 2.3 Add a public planning-failure regression in which the canonical managed filestore path is an escaping symlink; assert the configuration error occurs before an environment row is created or can reach `READY`.
- [x] 2.4 Run the focused environment checkout/removal tests and the repository's standard pull-request checks, confirming shared removal still neither drops the source database nor deletes the project filestore.
