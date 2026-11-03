## Context

Both `OdooInstance.run_tests` and the `odcli test` / `odcli module test` leaves use `_test_runner_source` to execute `odoo.tests.shell.run_tests()` once inside Odoo, normalize its report into a framed dictionary, and convert that dictionary into `OdooTestResult`. Odoo 19's `OdooTestResult` increments integer `failures_count` and `errors_count`; the generated source currently looks only for `failures` / `tests_failed` and `errors` / `tests_errors`. Missing attributes silently normalize to zero, so the existing shared converter receives false-green counts even though native Odoo logs an unsuccessful suite.

The base revision is `f0918da2d36b59c35cf0a949efc58744d1efb7f9`. The existing process boundary, framed payload, count normalizer, result model, CLI serializer, diagnostics redaction, and exit mapping are otherwise sufficient.

## Goals / Non-Goals

**Goals:**

- Read Odoo 19 failure and error counters before legacy fallbacks.
- Preserve non-negative integer/list normalization and the existing success, skipped, zero-test, diagnostic, and nonzero-exit behavior.
- Prove FAIL, ERROR, mixed, and pass-only outcomes at both deterministic lower-level boundaries and the real public Odoo 19 boundary.
- Keep `odcli module test` and top-level `odcli test` on the same runner and result contract.

**Non-Goals:**

- A second runner, result service, parser, selector, provisioning flow, or output schema.
- Inferring outcomes from log text, tracebacks, or shell return status alone.
- Changing database install/update/restore behavior, test discovery, tags, `--allow-empty`, progress rendering, or public signatures.
- Supporting unspecified future Odoo report fields beyond the existing fallbacks and the observed Odoo 19 counters.

## Decisions

### Prefer Odoo 19 counters in the existing generated source

The generated source SHALL read `failures_count` and `errors_count` first, then retain the current `failures` / `tests_failed` and `errors` / `tests_errors` fallbacks. The existing `_odcli_count` normalization SHALL remain the single normalization point for integer or collection-shaped values.

Alternative: inspect `wasSuccessful()` or parse native summary text. Rejected because neither preserves individual failed/error counts; text parsing is locale/version fragile and violates the existing native-report contract.

### Exercise the native report shape, not only normalized dictionaries

Focused tests SHALL execute or inspect the generated script with report objects exposing the actual Odoo 19 attribute names and SHALL assert the resulting framed counts. Existing converter tests using already-normalized dictionaries remain useful but are insufficient alone. Public CLI/SDK tests SHALL assert the process exit and JSON counts; the existing real-Odoo acceptance fixture SHALL add deterministic tagged pass/fail/error methods and reuse the focused public-leaf infrastructure.

Alternative: add only a converter unit test. Rejected because the defect occurs before conversion, while constructing the generated Odoo-shell payload.

### Keep one implementation and verification slice

The code, lower-level regression, real-Odoo fixture, public-boundary acceptance, and CI inventory updates form one deliverable because each is small and they share the same runner contract. No new dependency or subsystem is introduced.

Alternative: introduce a report adapter class or split the runner by Odoo version. Rejected as unnecessary indirection for additive attribute precedence.

## Ponytail Full Review

No new subsystem is necessary. Existing alternatives are `_test_runner_source`, `_odcli_count`, `run_odoo_tests_command`, the `odcli_e2e_probe` fixture, and `E2E-FC-08`; all are retained and extended in place. A new adapter, runner, dependency, configuration option, or log parser is removed from consideration because it adds no required behavior.

## Risks / Trade-offs

- [Fallback precedence could regress an older report object exposing multiple names] → make precedence explicit and cover Odoo 19 integer counters plus legacy integer/list fallbacks in a parameterized matrix.
- [Intentional failing/error fixture methods could make the harness itself fail] → invoke explicit tags and assert the CLI's expected nonzero status and decoded result; keep the ordinary passing probe selection isolated.
- [A shell process can return zero while native tests fail] → assert both the native counts and the public process exit rather than relying on the child shell code.
- [Visible output differs between baseline and candidate] → retain exact baseline/candidate text and render genuine captures with the bundled terminal renderer; label ANSI/TTY limitations.

## Migration Plan

No data or configuration migration is required. Publish the focused code/test change, run lower-level checks, then verify on a fresh numbered Odoo 19 slot pinned to the exact candidate SHA. Rollback is a normal code revert; no persistent state was added.

## Open Questions

None. The observed Odoo 19 field names, existing fallbacks, public result schema, and required acceptance cases are fixed by GitHub issue #197.
