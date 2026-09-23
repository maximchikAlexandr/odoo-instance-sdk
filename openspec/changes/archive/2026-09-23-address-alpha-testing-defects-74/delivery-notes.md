# Delivery notes — section 19 (address-alpha-testing-defects-74)

CI baseline: GitHub Actions run [35857144617](https://github.com/maximchikAlexandr/odoo-instance-sdk/actions/runs/35857144617).

## Local gates (2026-09-23)

| Gate | Result | Notes |
| --- | --- | --- |
| `tests/unit/test_documentation_contract.py` | **pass** (11/11) | After README/SDK/execution-boundary updates |
| `make lint` | **fail** | Pre-existing: `tests/unit/internal/test_transport.py` would reformat (not touched in section 19) |
| `make types` | **not re-run** | Blocked after lint format check |
| `make test` | **pass** after fix | 3256 passed; initial failure from README `test_instance.database` example — fixed `test_readme_preparation_manifest_parses_and_roundtrips` |
| `make compat` | **pass** | 3256 passed + 13 serial passed, 2 skipped |
| `uv build` | **pass** | `odoo_instance_sdk-0.1.0-py3-none-any.whl`, `odoo_instance_sdk-0.1.0.tar.gz` |
| `make package` | **fail** | `tests/packaging/test_self_update.py::test_uv_tool_full_update_from_old_revision` — not fixed per instruction (no CI regression fixes) |
| `make dashboard` / `make smoke` / `make live` | **skipped** | External prerequisites (Node dashboard, Docker smoke, real Odoo) |

## OpenSpec

- Delta specs synced to `openspec/specs/` via `openspec archive` (26 added, 25 modified requirements).
- Change folder restored to `openspec/changes/address-alpha-testing-defects-74/` because section **17** (E/F test cleanup) remains open — permanent archive deferred.
- `openspec validate --specs`: 34 passed, 0 failed.

## PR

- [#79](https://github.com/maximchikAlexandr/odoo-instance-sdk/pull/79) updated with implementation + documentation summary; links #74.
