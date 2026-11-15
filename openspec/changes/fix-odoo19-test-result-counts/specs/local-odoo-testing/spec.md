## MODIFIED Requirements

### Requirement: Result, diagnostics, and exit semantics

The test operation SHALL derive `OdooTestResult` only from the native runner report and process outcome, not by matching words or regular expressions in logs. For Odoo 19 reports it SHALL read the non-negative integer `failures_count` and `errors_count` fields. When those fields are absent, it SHALL retain the supported `failures` / `tests_failed` and `errors` / `tests_errors` integer-or-collection fallbacks. If more than one supported field is present for a counter, the Odoo 19 `*_count` field SHALL take precedence. `failures` SHALL be true when normalized native failed or error counts are non-zero. `zero_tests` SHALL be true when the native test count is zero. `exit_code` SHALL be non-zero for runner/process failure, failed/error tests, and zero tests unless `allow_empty=True`; `allow_empty` SHALL affect only the zero-test case.

Sanitized native Odoo failure diagnostics SHALL remain a stderr stream and SHALL NOT be inserted unsanitized into the typed result or machine stdout document. Renderer choice SHALL not change selection, preflight, runner invocation, result fields, or exit code.

The command result SHALL use exactly these state-dependent shapes:

| State | Required common fields | Required state fields | Execution-only fields |
| --- | --- | --- | --- |
| `executed` | resolved environment identity/worktree, selector kind/value/provenance, modules, `exit_code` | effective `test_tags`, `reload_tests`, `allow_empty` | `counts`, `failures`, and `zero_tests` SHALL be present and SHALL come from `OdooTestResult` |
| `dry_run` | resolved environment identity/worktree, selector kind/value/provenance, modules, `exit_code=0` | `dry_run=true` and complete base/Git provenance | `test_tags`, `reload_tests`, `allow_empty`, `counts`, `failures`, and `zero_tests` SHALL be absent |
| `no_addon_changes` | resolved environment identity/worktree, selector kind/value/provenance, empty `modules`, `exit_code=0` | `reason="no_addon_changes"` and complete base/Git provenance | `test_tags`, `reload_tests`, `allow_empty`, `counts`, `failures`, and `zero_tests` SHALL be absent |

`dry_run` with no addon changes SHALL use the `no_addon_changes` shape and MAY additionally contain `dry_run=true`. These non-executed success states SHALL NOT construct an `OdooTestSpec` or `OdooTestResult`, synthesize zero counts or false flags, emit Odoo diagnostics, or display execution progress. JSON and strict-decoded TOON SHALL be semantically equal for every state.

#### Scenario: Non-executed success does not fabricate a result

- **WHEN** a safe changed dry-run selects addons, or a non-dry changed selection finds no addon changes
- **THEN** its machine result follows the corresponding state shape, exits `0`, omits every execution-only field, and emits no runner progress or Odoo diagnostics

#### Scenario: Zero tests requires explicit allowance

- **WHEN** the native runner reports zero tests
- **THEN** the result has `zero_tests=true` and a non-zero exit code unless `allow_empty=true`, in which case it exits `0`

#### Scenario: Odoo 19 failure and error counters drive the result

- **WHEN** an Odoo 19 native report returns `failures_count=1` or `errors_count=1` while the shell process itself returns zero
- **THEN** the corresponding normalized count is `1`, `successful` excludes it, `failures=true`, the command exits non-zero, and sanitized native diagnostics remain on stderr

#### Scenario: Mixed Odoo 19 outcomes preserve every count

- **WHEN** the native Odoo 19 report contains one passing test, one failure, and one error with no skipped tests
- **THEN** the result contains `tests=3`, `successful=1`, `failed=1`, `errors=1`, `skipped=0`, `failures=true`, and a non-zero exit code

#### Scenario: Passing Odoo 19 selection remains successful

- **WHEN** the native Odoo 19 report contains one passing test and zero failures, errors, and skipped tests
- **THEN** the result contains `tests=1`, `successful=1`, `failures=false`, and exit code `0`

#### Scenario: Legacy native report shapes remain normalized

- **WHEN** a native report lacks Odoo 19 counter fields and exposes a supported integer or collection-shaped legacy failure/error field
- **THEN** the existing count normalization and failure/exit semantics remain unchanged

#### Scenario: Renderer does not infer success from logs

- **WHEN** diagnostics contain success- or failure-like words inconsistent with the native report
- **THEN** counts and exit code follow the native report/process status and no regex log gate changes them
