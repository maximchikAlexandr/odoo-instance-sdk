# E/F test inventory — `address-alpha-testing-defects-74` (item 17)

Scan scope: test files touched on `feat/issue-74-alpha-testing-defects-batch2` vs merge-base `9a862fbe8ff83d57c452144badbd178327d5e598` (`git diff --name-only 9a862fbe...HEAD -- tests/`). **58** Python test modules in scope (fixtures and markdown excluded from grading).

Grading heuristics (design D17):

- **F**: only `assert True`, or implicit “did not raise” with no other assertion (`pytest.raises` counts as an assertion and is **not** F).
- **E**: every assertion is on `_`-prefixed names or mock `assert_called*` of private helpers only.

## Merge-base coverage baseline

Zone line / branch thresholds from `pyproject.toml` `[tool.coverage.thresholds]` / `[tool.coverage.branch_thresholds]` on merge-base `main` (same contract enforced by `make coverage`):

| Zone | Line % | Branch % |
|------|--------|----------|
| cli | 71 | 53 |
| process | 74 | 59 |
| environment | 76 | 57 |
| catalog | 92 | 83 |
| security | 94 | 89 |
| postgres | 80 | 70 |
| monitor | 80 | 70 |
| developer_workflow | 60 | 40 |

Post-cleanup verification: focused pytest on changed test files; zone thresholds must not regress (no cosmetic `pragma: no cover` gaming).

## CI note (task 17.5)

Baseline CI for this branch: run **35857144617** (post-fix). `offline-tests` and macOS compat jobs may still fail — accepted per PM; not in scope for item 17.

## Inventory

| nodeid | grade | decision | reason |
|--------|-------|----------|--------|
| `tests/unit/test_monitor_snapshot.py::test_watch_cancellation_cleans_up` | F | rewritten | Async `watch()` cancellation had no assertion beyond “did not crash”; now asserts a real snapshot is yielded and the generator is exhausted after `aclose()`. |
| `tests/unit/internal/test_transport.py::test_open_odoo_http_client_delegates_to_for_origin` | E | deleted | Only `for_origin.assert_called_once_with` plus mock identity; `open_odoo_http_client` is already exercised by health/database characterization tests through the injected transport boundary. |
| `tests/unit/internal/test_transport.py::test_injected_transport_boundary_is_used_for_health` | E | deleted | Duplicate of `tests/unit/internal/test_health.py::test_database_manager_readiness_retries_and_accepts_empty_list` (weaker subset: no endpoint/method assertion). |
| `tests/unit/test_cli_multi_target_deletion.py::test_multi_target_deletion` | — | kept | Parametrize shell delegates to scenario runners with behavioural assertions; not F. |
| `tests/integration/real_odoo/test_critical_path.py::test_catalog_patch_targets_are_importable` | — | kept | Delegates to `_assert_catalog_patch_targets_importable()` which asserts importability of patch targets. |
| `tests/unit/storage/test_catalog_migration.py::test_reopen_stamped_catalog_is_idempotent` | — | kept | `_assert_current_revision` asserts `catalog_revision(conn) == CATALOG_REVISION` (schema contract, not mock-only). |
| `tests/unit/test_cli_output_modes.py::test_rich_dry_run_uses_real_command_builders` | — | kept | `_assert_actual_builder_rich_preview` checks rendered process order, preconditions, and zero execution. |
| `tests/unit/test_cli_output_modes.py::test_rich_dry_run_uses_actual_environment_checkout_builder` | — | kept | Same helper; behavioural Rich dry-run contract. |
| All other tests in the 58-file scan scope | — | kept | Reviewed; no F (no bare `assert True`, no assertion-free bodies) and no E (assertions are not exclusively private mock wiring). |

## Summary

- **F handled**: 1 rewritten
- **E handled**: 2 deleted (coverage preserved in `test_health.py` and transport characterization tests)
- **Skips/xfails/weakened assertions added**: none
