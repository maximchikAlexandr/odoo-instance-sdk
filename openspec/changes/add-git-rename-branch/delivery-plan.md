## Delivery Basis

- Change: `add-git-rename-branch`, planning revision at this commit.
- Approved base: `origin/main` at `f0918da2d36b59c35cf0a949efc58744d1efb7f9`.
- Estimate source of truth: the root planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; this document intentionally does not duplicate numeric totals.
- Estimate basis: remaining implementation from the approved base for one experienced developer familiar with the project stack, without AI acceleration. Confidence, assumptions, and evidence are recorded below after the mandatory estimate read-back.

## E2E Decision

`e2e_required=true`.

Repository-owned integration tests with disposable local/bare Git repositories are sufficient for allocation arithmetic, local/catalogue/origin collisions, races, detached HEAD, dirty/index preservation, upstream cleanup, and compensation. A focused independent Mac walkthrough remains required because the change introduces visible Rich terminal output and a public selector-to-SDK-to-real-Git path; lower-level assertions cannot produce the mandatory genuine before/after output evidence. No Odoo process, database, PostgreSQL cluster, or numbered `odcli-N` slot is required because the specified operation must not create or inspect those resources.

## Verification Matrix

| Case ID | OpenSpec requirement/scenario | Verification gap / local E2E decision | OpenAPI operation/schema | Local test script | Independent agent steps | CI disposition / test / job | Assertions | Artifacts / images |
|---|---|---|---|---|---|---|---|---|
| GRB-01 | Shared allocation: first/max/gaps and unavailable sources | Lower-level deterministic coverage is sufficient; no live stand | N/A | `uv run pytest -q -o addopts='' tests/unit/test_cli_ticket_checkout.py tests/unit/test_ticket_branch_allocation.py` | Inspect test fixtures and compare shared allocator use from checkout and rename; do not rerun as the manual check | Extend `offline-tests` through the named unit nodes | Exact unsuffixed/`_N` selection, maximum non-reuse, source failure is non-empty error, checkout parity | Pytest command/exit and candidate SHA in Multica only; no image |
| GRB-02 | Safe rename: free/local/catalogue/origin/multiple/repeat/detached/race/state preservation | Disposable repository integration closes mutation/race gap without Odoo | N/A | `uv run pytest -q -o addopts='' tests/integration/test_git_rename_branch.py` | Independently inspect before/after refs, HEAD, index tree, porcelain status, untracked file, catalogue rows, and bare-origin refs for one passing fixture | Extend `offline-tests`; add focused integration node, no new framework | Non-force rename only; no switch/overwrite; repeated call no-op; stale plans fail; state bytes/refs unchanged except local branch/upstream | Command/exit, fixture hashes, and assertions in Multica only; no decorative image |
| GRB-03 | Upstream-safe local-only behavior and compensation | Lower-level fault injection is sufficient for cleanup failure; real push is prohibited | N/A | Same integration node as GRB-02 plus focused `-k upstream` | Inspect the planned argv/action sequence and one injected cleanup failure; verify old branch/config restoration or explicit incomplete recovery | `offline-tests` | No fetch/push/remote ref/worktree/catalogue write; mismatched upstream absent after success; bounded non-overwriting compensation | Structured assertion summary in Multica only; no image |
| GRB-04 | CLI leaf: selectors, dry-run, Rich/JSON/TOON, bounded failure, inventory/docs | Automated CLI contracts cover envelopes; live case required for real selector/origin and visible output | N/A | `uv run pytest -q -o addopts='' tests/unit/test_cli_git.py tests/unit/test_cli_output_modes.py tests/unit/test_cli_characterization.py tests/unit/test_git_architecture.py tests/unit/test_verification_assets.py` | On Mac, use isolated baseline/candidate SDK checkouts and a disposable clone of `<PROJECT_TEST_REPOSITORY_URL>` resolved from the Multica project description; create equivalent non-ticket starting branches, capture baseline command-not-present text at the approved base, then candidate dry-run and one successful rename using `odcli --project "$CHECKOUT" git rename-branch ICODE-596`; inspect refs/state and clean only the disposable checkout | Extend existing `offline-tests` and `compatibility` jobs; `PUBLIC_LEAF_CASES` disposition `not-applicable` for Real Odoo with rationale that this is isolated Git workflow evidence | Baseline and candidate revision recorded; dry-run leaves refs/config unchanged; success returns old/selected names and preserves HEAD/index/porcelain; origin unchanged; help/inventory/docs agree | `git-rename-branch-before.txt/.png`, `git-rename-branch-dry-run.txt/.png`, `git-rename-branch-after.txt/.png`; Multica attachments only |

## Implementer-Owned Verification Assets

- Add `tests/unit/test_ticket_branch_allocation.py` only if extraction from `test_cli_ticket_checkout.py` makes ownership clearer; do not retain duplicate matrices.
- Add `tests/integration/test_git_rename_branch.py` using temporary working and bare repositories plus an isolated temporary catalogue. The test SHALL require no private URL, credentials, Odoo runtime, database, or network.
- Extend existing CLI/output/architecture/documentation fixtures and `.github/workflows/ci.yml` only as needed to run the tests through existing jobs. Do not add a new runner or workflow.
- The Implementer owns all test code, fixtures, README changes, and CI wiring. The independent Tester SHALL not edit them; findings return to the Implementer.

## Independent Tester Contract

### Readiness gate

Before a live attempt, the Tester SHALL record the candidate product SHA, approved baseline SHA, test-code SHA, clean SDK checkouts, exact private test-repository resource resolved from the project description, an isolated disposable checkout/branch, locale, terminal width, output mode, and passing focused deterministic tests. Private hostnames, credentials, user identity, and local absolute paths SHALL be sanitized from evidence.

### Walkthrough

1. Capture baseline text for the exact command at the approved base, where the leaf is absent; do not invent a success baseline.
2. At the candidate SHA, create an equivalent disposable starting branch with one committed file, one staged change, one unstaged change, and one untracked file. Record HEAD, index tree, porcelain status, current branch, upstream, and matching origin refs.
3. Run candidate Rich dry-run and verify every recorded state item remains byte-for-byte equivalent.
4. Run the candidate command once, verify returned old/selected names, unchanged HEAD/index/porcelain/untracked content, changed local current branch only, absence of mismatched upstream, unchanged origin refs, and unchanged catalogue row count.
5. Invoke the same ticket again and verify a no-op result with no later suffix.
6. Render genuine captured text with the bundled `openspec-e2e-testing/scripts/render_terminal.swift`. Preserve the tested output language; agent-authored captions and the final report remain English. ANSI/TTY/progress omissions SHALL be stated honestly when redirected output cannot preserve them.

The live budget is two attempts: one planned attempt and one repair attempt only after publishing the first failure diagnostics and rerunning cheaper deterministic checks. A PASS on the same product/test revisions and fixtures is retained; a new product SHA requires an impact assessment and rerun of affected cases only.

### Evidence publication and cleanup

The final Multica Tester comment SHALL include the candidate/baseline/test revisions, commands and exit codes, sanitized actual text, assertion results, before/dry-run/after PNG attachments with read-back, and links to the implementation PR/MR, commits, and changed test files. Logs, diagnostic JSON, hashes, provenance, screenshots, and assertion results SHALL remain in Multica and SHALL NOT be committed. No function-produced Git commit is expected from branch rename, so no separate test-repository result commit is required. Cleanup removes the disposable checkout and temporary local resources but SHALL NOT close/merge the evidence PR/MR or delete its branch without separate authority.

## Delivery Topology

`delivery_mode: single_wp_no_dag`

The authoritative `Estimate, hours` property was written and read back successfully and is within the mandatory single-package threshold. The implementation SHALL therefore use exactly one work package. No stage field is created. OpenSpec tasks remain the stable scope; operational status and assignee data remain outside this change.

### WP-01 — Deliver ticket-safe current-branch rename

- Task coverage (complete and one-time): `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `2.4`, `3.1`, `3.2`, `3.3`, `4.1`, `4.2`, `4.3`.
- Deliverable: one reviewed implementation that shares checkout allocation, exposes the public SDK rename command and convenience operation, registers the bounded CLI leaf, preserves local Git state, cleans mismatched upstream safely, and includes all tests, docs, and CI wiring required by the OpenSpec.
- Responsibility scope: shared ticket-allocation ownership; `commands/env/checkout.py`; `resources/git.py`; public Git models and exceptions; Git/catalogue/process helpers; CLI adapter; test-owned leaf inventory; focused unit/integration/architecture/output/documentation tests; README and directly related fixtures/CI metadata. Critical shared files are `src/odoo_instance_sdk/resources/git.py`, `src/odoo_instance_sdk/commands/env/checkout.py`, `src/odoo_instance_sdk/commands/git.py`, `tests/unit/test_cli_output_modes.py`, and `README.md`.
- Contract surface: the two delta specs, unchanged main `development-environment` allocation semantics, `GitResource.rename_branch_command()` / `rename_branch()`, frozen rename result, existing root project/environment selectors, bounded Rich/JSON/TOON envelope, `internal/proc`, and `PUBLIC_LEAF_CASES` as the sole CLI inventory authority.
- Definition of done and evidence: all OpenSpec tasks checked only after implementation; focused tests prove every scenario; repository lint/type/architecture/test gates satisfy the ten-minute local-run policy with longer required suites on published exact-revision CI; strict OpenSpec validation passes; independent Tester completes GRB-01 through GRB-04 and publishes required Multica evidence/attachments with read-back.
- Parallel-safety rationale: the threshold mandates one package, and the core extraction plus `GitResource`, CLI inventory, and checkout regression work share write-hot files and invariants. Splitting them would create artificial coordination without an independent deliverable.

## Estimate Confidence and Evidence

Confidence is medium; calibration is not applicable because the required basis is one experienced human developer familiar with the stack, without AI acceleration. The estimate covers remaining work from the approved base and was derived from non-overlapping units for shared allocation extraction, SDK rename/revalidation/compensation, focused tests and fixtures, CLI/inventory/docs, and verification/review repair.

Evidence includes the existing ticket implementation in `commands/env/checkout.py`, the mature inspectable command patterns in `resources/git.py`, existing allocation tests in `tests/unit/test_cli_ticket_checkout.py`, Git resource/CLI/integration analogues, and existing offline/macOS compatibility CI jobs. Uncertainty is concentrated in safe post-rename upstream compensation, exact catalogue locking integration, and maintaining current checkout plan/provenance without regressions. No tests were run for the estimate. Material expansion to remote rename, generic explicit repositories, alternative ticket formats, or Odoo/database effects invalidates it.
