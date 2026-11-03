## Delivery Contract

- Change: `fix-odoo19-test-result-counts`
- Planning issue: `MYL-636`
- Approved base: `f0918da2d36b59c35cf0a949efc58744d1efb7f9` (`origin/main`)
- Estimate source: the root planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numeric totals are intentionally not duplicated here.
- Confidence: high for the implementation boundary and medium-high overall because the exact Odoo 19 fields and existing shared converter are inspected, while live fixture integration can still expose bounded tag/runner friction.
- Delivery mode: `single_wp_no_dag`, selected from the verified `Estimate, hours` property under the authoritative threshold contract.
- Work package: exactly `WP-01`; no `depends_on`, stage, frontier, or DAG metadata exists.

### WP-01 — Correct and prove Odoo 19 test outcomes

- Task coverage: `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2` exactly once.
- Deliverable: the existing single runner correctly normalizes Odoo 19 and supported legacy report shapes, public SDK/CLI results and exits are accurate, and deterministic plus real-Odoo evidence covers failure, error, mixed, pass-only, alias, zero-test, and skipped regressions.
- Owned responsibility scope: generated runner normalization and its directly related unit/CLI tests; `odcli_e2e_probe` test fixture; focused real-Odoo handler/inventory/acceptance mapping; applicable CI configuration and documentation. Directly related fixtures, snapshots, or helper updates are included; unrelated runner, lifecycle, catalog, and output work is excluded.
- Critical shared files: `src/odoo_instance_sdk/internal/automation.py`, `tests/unit/resources/test_cli_automation.py`, `tests/unit/commands/test_test.py`, `tests/fixtures/addons/odcli_e2e_probe/`, `tests/integration/real_odoo/focused_handlers.py`, `tests/integration/real_odoo/command-matrix.md`, `scripts/real_odoo_acceptance.py`, and the existing real-Odoo workflow if its established selection needs adjustment.
- Contract surface: `OdooInstance.run_tests`, `run_odoo_tests_command`, `OdooTestResult`, `odcli test`, and `odcli module test`; signatures and serialized field names remain unchanged.
- Definition of done/evidence: all OpenSpec tasks complete; focused lower-level checks pass; exact candidate SHA is published; established CI is triggered without local runs beyond the ten-minute automation limit; Tester posts real Odoo 19 counts, process exits, sanitized stderr observation, alias equivalence, provenance, MR/commit/file links, and required text/PNG attachments with read-back; cleanup removes only the disposable slot.
- Parallel-safety rationale: one WP is mandatory under the estimate threshold and avoids concurrent writes to the shared runner/fixture/acceptance slice.

## E2E Verification Plan

`e2e_required=true`. Unit execution of the generated source can prove field precedence, but it cannot alone prove that the shipped public CLI observes Odoo 19's actual `OdooTestResult`, framed shell transport, native diagnostics, and process exit together. GitHub issue #197 explicitly requires the public CLI/SDK boundary, and the visible JSON/exit correction requires a real baseline/candidate capture.

The test repository URL SHALL be resolved at execution time from the Multica project description (`TEST_REPO_URL`); do not persist its private hostname or credentials in Git. Tester SHALL use a fresh `odcli-N` numbered slot, its own checkout/database/HTTP port, the numbered project profile, the clean reference checkout, and the exact candidate SDK SHA. The reference checkout and primary development database SHALL NOT be used as the disposable worktree or database.

### Coverage matrix

| Case | Requirement / scenario | Gap and disposition | Local script | Independent actions | CI disposition | Assertions | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `TC-01` | Odoo 19 failure counter | Live E2E required: actual `failures_count` transport is not proven by mocks | `tests/e2e/test_myl636_odoo19_results.py::test_failure` in the test repo | Run explicit FAIL tag through `odcli test --format json`; independently inspect JSON, stderr, and shell exit | Extend existing focused real-Odoo case/job with the same explicit tag | `tests=1`, `successful=0`, `failed=1`, `errors=0`, `failures=true`, nonzero exit; native failure diagnostic stays on stderr | JSON projection, sanitized stderr text, command/exit, candidate SHA, after PNG |
| `TC-02` | Odoo 19 error counter | Live E2E required: actual `errors_count` transport is not proven by mocks | `tests/e2e/test_myl636_odoo19_results.py::test_error` | Run explicit ERROR tag and inspect the same boundaries | Same existing focused job | `tests=1`, `successful=0`, `failed=0`, `errors=1`, `failures=true`, nonzero exit | JSON projection, sanitized stderr text, command/exit, candidate SHA, after PNG |
| `TC-03` | Mixed outcomes | Live E2E required to prove aggregation across the native suite | `tests/e2e/test_myl636_odoo19_results.py::test_mixed` | Run class tag selecting PASS/FAIL/ERROR, compare native summary with decoded CLI result | Same existing focused job | `tests=3`, `successful=1`, `failed=1`, `errors=1`, `skipped=0`, nonzero exit | JSON projection, native summary excerpt, command/exit, candidate SHA |
| `TC-04` | Passing selection and alias | Live E2E required as regression guard at both public leaves | `tests/e2e/test_myl636_odoo19_results.py::test_pass_and_alias` | Run PASS tag via top-level `test`, then equivalent `module test`; compare result fields and exits | Existing `E2E-FC-08`, extended only as necessary | each result has one success, zero failure/error, `failures=false`, exit 0; aliases agree | Both JSON projections, commands/exits, candidate SHA |
| `TC-05` | Legacy precedence, zero tests, skipped, log independence | Lower-level checks sufficient; real infrastructure adds no distinct assertion | none | Inspect focused pytest results and changed tests; do not repeat as live cases | `offline-tests` focused unit/CLI matrix | Odoo 19 fields win when both exist; legacy list/integer fallback, zero-test/allow-empty, skipped, and log-word behavior remain correct | pytest node IDs/results and CI link only |

There is no OpenAPI operation or schema in this change.

### Repeatable test inputs and scripts

Implementer owns the repository fixture and CI extensions in tasks `2.2`–`2.3`; Tester does not edit them. Tester owns a test-repository script `tests/e2e/test_myl636_odoo19_results.py` plus a portable addon `addons/odcli_result_probe/` whose root imports `tests` and whose `tests/__init__.py` imports `test_result`. The tagged Odoo 19 `TransactionCase` contains exactly `test_pass`, `test_fail`, and `test_error`, with stable intentional messages. Commit these test inputs in one ordinary test-code commit and record `TEST_CODE_SHA`. If the verified Git utility creates a functional commit, preserve that commit unchanged and link it separately; if no repository-native functional result exists, do not create a second commit.

Readiness gate before the single live attempt: candidate branch is published and `PRODUCT_SHA` matches its remote; lower-level regression and review gates for that SHA are green or explicitly recorded; the dedicated PostgreSQL cluster is healthy on its configured private endpoint; chosen slot/database/port are unused; the test checkout is clean at `TEST_CODE_SHA`; the addon is installed in the copied disposable database; the slot manifest proves `PRODUCT_SHA`, checkout revision, isolated database, and unique port. Do not run tests during planning.

The script invokes the slot's pinned launcher with explicit project, module, tags, `--reload-tests`, and `--format json`, captures stdout and stderr separately, records the actual exit status without `set -e` swallowing expected nonzero results, decodes the JSON envelope, and asserts the matrix above. It also calls the public SDK operation in the same isolated runtime for one failing selection and asserts equality with the CLI result surface. Setup or assertion failure preserves sanitized evidence before failure-safe cleanup.

### Independent walkthrough and images

Tester SHALL perform fresh commands rather than merely rerunning the automated script: `TC-01`, `TC-02`, `TC-03`, and `TC-04` with explicit tags. For `TC-01`, capture equivalent baseline and candidate commands against identical fixture revision, locale, terminal width, non-interactive JSON mode, and reset database state between revisions. Preserve the actual UTF-8 stdout/stderr text as `myl636-baseline-failure.txt` and `myl636-candidate-failure.txt`. Render those genuine texts to `myl636-baseline-failure.png` and `myl636-candidate-failure.png` with the bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`; label them rendered terminal text, not terminal-window screenshots. The baseline must show the false-green result and exit 0; the candidate must show the corrected count and nonzero exit. Capture candidate ERROR output as `myl636-candidate-error.txt` / `.png` because error reporting visibly changes too.

Record that JSON redirection does not preserve TTY-only color, spinner, or interactive progress; those states are out of scope and SHALL NOT be claimed. Captured text remains in the output's original language; agent-authored explanation remains English. Sanitize private paths, hostnames, database names, and personal data without altering result counts or exit values. Missing baseline or any mandatory capture blocks complete verification.

### Publication, attempts, and cleanup

Tester SHALL post to the Multica implementation issue and verify comment/attachment read-back: `PRODUCT_SHA`, `BASELINE_SHA`, `TEST_BASE_SHA`, `TEST_CODE_SHA`, optional untouched `FUNCTIONAL_SHA`, slot/fixture provenance with private identifiers sanitized, exact commands and exit statuses, decoded assertions, relevant CI URLs, evidence MR/branch/commit/file links, and the three text/PNG pairs. Raw logs, diagnostic JSON, hashes, and images live only in Multica; do not commit them. GitLab retains only test scripts/fixtures and any genuine function-produced commit.

Use one planned live attempt after readiness. A setup failure before test invocation does not claim a product result; preserve diagnostics and repair deterministically before any authorized retry. Product changes invalidate affected live cases and require impact assessment; unchanged cases on the same revision remain valid. Cleanup uses the Binta wrapper with the same numbered profile to remove the disposable checkout/database/filestore/processes while preserving the evidence branch, MR, commits, and Multica attachments. Do not close/merge the MR or delete its branch without a separate instruction.
