## Why

Odoo 19 records native test failures and errors in `failures_count` and `errors_count`, but the generated OdCLI runner ignores those fields and can report a failed suite as successful with exit code 0. This false-green result can cause developers and CI agents to accept broken application changes.

## What Changes

- Normalize Odoo 19 failure and error counters from the native `OdooTestResult` returned by the existing single runner.
- Preserve the deliberately supported integer/list-shaped legacy counter fallbacks, skipped and zero-test semantics, sanitized diagnostics, and the shared nonzero-exit conversion.
- Add regression coverage at the generated native-report boundary and through the public `odcli test` / `OdooInstance.run_tests` result path, including the `module test` compatibility alias.
- Verify FAIL, ERROR, mixed PASS/FAIL/ERROR, and pass-only selections against a disposable Odoo 19 instance.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `local-odoo-testing`: Require native Odoo 19 `failures_count` and `errors_count` to drive normalized counts, failure state, and process exit semantics while retaining supported fallback report shapes.

## Impact

The change is limited to the generated native test runner in `src/odoo_instance_sdk/internal/automation.py`, its focused unit/CLI regression coverage, the existing real-Odoo fixture and focused acceptance path, and the `offline-tests` / real-Odoo CI jobs that exercise those tests. Public method signatures, result schemas, dependencies, database lifecycle, selectors, output formats, and process boundaries do not change.
