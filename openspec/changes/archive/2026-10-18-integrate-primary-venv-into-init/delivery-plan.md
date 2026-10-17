# Delivery plan

## Planning baseline

- Task key: `MYL-505`.
- OpenSpec change: `integrate-primary-venv-into-init`.
- Approved implementation base: updated `origin/main` at `884aa66c711078879f9e540256f3896eaf0b6a39`, the merge commit for PR #152.
- Planning branch/worktree: the dedicated MYL-505 managed checkout; production code is unchanged.
- Test repository: resolve the private URL from the current Multica project description at execution time. Do not place its hostname, credentials, personal data, or private URLs in public Git artifacts.
- Existing foundation retained: public `prepare_venv_command`/`prepare_venv`, deterministic discovery, uv prepared steps, project/venv locks, trusted origin, staged lock publication, manifest-byte restoration, platform interpreter resolution, init manifest/config handling, Compose lifecycle, Odoo bootstrap, output envelopes, and canonical CLI inventories.

## Estimate contract

- Authoritative totals SHALL exist only in the root planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; this file intentionally does not duplicate them.
- Basis: remaining work from the approved base to all acceptance criteria for one experienced developer familiar with Python, Click, uv, and this repository, without AI acceleration. Active hours include investigation, implementation, tests, documentation, likely review fixes, and attended verification; unattended CI/service waits and approval queues are excluded.
- Confidence: medium. The affected command builders, callbacks, persistence helpers, tests, CI jobs, and close analogues are directly inspectable; the main uncertainty is safely sharing the preparation transaction with a not-yet-written fresh manifest while retaining all failure restoration and existing-manifest branches.
- Calibration: uncalibrated because no comparable executor timing history was supplied.
- Evidence: `project_init.py` command composition and preparation transaction; init registration and existing-manifest decisions; trusted venv-origin and manifest/lock helpers; dependency discovery; focused preparation, self-contained init, PostgreSQL init, output-mode and inventory tests; Windows preparation and compatibility CI; the complete OpenSpec/E2E package.
- Material assumptions: no new public result model or venv catalog is required; existing uv argv and dependency discovery are reusable; the merged PR #152 behavior is accepted foundation; private test-repository access and the existing Compose/Odoo prerequisites are available to Tester. Replacing any of those assumptions requires re-estimation.
- Scope coverage: implementation, focused and regression tests, Windows/POSIX coverage, documentation, local E2E script/fixtures, attended verification and likely review repair are included. Unattended CI time, human approval queues, unrelated PR #152 repair, deployment, and new venv lifecycle features are excluded.
- Tests SHALL NOT be run to produce the estimate.

## Delivery topology

Delivery mode: `single_wp_no_dag`.

The authoritative `Estimate, hours` property selects the mandatory single-package mode. Manager SHALL create exactly one implementation WP child after independent plan verification. It SHALL have no `depends_on`, stage, execution frontier, or DAG orchestration.

### WP-01 — Implement and verify integrated init venv preparation

- Task coverage: every OpenSpec task `1.1`–`4.3`, each covered exactly once.
- Independent deliverable: one complete SDK-first change in which opted-in init captures and executes manifest → primary-venv preparation/persistence → Compose/Odoo bootstrap, while omitted venv input and standalone recovery remain compatible.
- Owned responsibility scope: public/internal project-init composition; the minimum shared preparation seam; venv origin, manifest and lock integration; bootstrap Python selection; init CLI binding/existing-manifest/output paths; directly related unit/integration/contract tests, fixtures, snapshots, Windows/compatibility CI edits, documentation, and the private test-repository E2E script/fixtures. This is a responsibility scope, not an exhaustive file allowlist.
- Contract surface: optional public init venv parameter and `--venv`; resolved Python creation selector; actual platform interpreter; immutable ordered plan; atomic preparation persistence; phase-consistent partial state; existing confirmation/no-op/repair/resume semantics; Rich/JSON/TOON and standalone `prepare-venv` compatibility.
- Definition of done / evidence: every delta scenario maps to a focused passing repository check or an explicit row in the verification matrix; omitted-option behavior is unchanged; no second venv implementation or unplanned effect exists; required GitHub gates and E2E assertions pass; exact product/test revisions, commands, exit codes, links, sanitized observations, and required images are published and read back in Multica.
- Parallel-safety rationale: not applicable. The threshold contract requires one WP, and the shared manifest/preparation/bootstrap transaction plus canonical CLI/test surfaces form one coherent ownership boundary.

## E2E decision

`e2e_required=true`.

Focused unit/integration/contract tests are sufficient for missing-Python rejection, venv-path and interpreter projections, immutable plan identity, no-option compatibility, every injected failure and byte restoration boundary, empty inputs, output envelopes, and POSIX/Windows behavior. One local E2E is still required because no cheaper check proves the real interaction among a fresh manifest, a real uv-created primary venv, persisted trusted origin/lock state, Compose startup, and Odoo bootstrap through the newly prepared interpreter. The accepted PR #152 evidence predates `init --venv` and SHALL NOT count as this change's result.

Planning SHALL NOT execute tests. Tester SHALL use a dedicated `odcli-N` slot with its own product checkout, test-repository checkout, XDG/catalog roots, Compose project, database, port, and venv. The reference checkout and any pre-existing database SHALL remain untouched.

## Verification matrix

| Case ID | OpenSpec requirement/scenario coverage | Verification gap and disposition | Local script | Independent Tester actions | CI disposition / assertions | Artifacts and images |
| --- | --- | --- | --- | --- | --- | --- |
| E2E-01 | `project-init`: fresh ordered preparation, prepared interpreter drives bootstrap, empty/non-empty preparation, successful-state retry; `development-environment`: shared owner; `cli-odcli`: delegation, dry-run, existing refresh | Local E2E required for the combined real uv + manifest + Compose/Odoo boundary | `tests/e2e/myl505_init_venv_e2e.py` in the dedicated private test-repository branch/MR; invoke with explicit product checkout, slot, Python selector, venv, Compose image/port, fixed locale and output directory | From a clean dedicated slot, capture candidate `init --help`; create controlled addon/explicit requirements; run opted-in JSON dry-run and prove no files/processes; execute fresh self-contained init into a missing venv; inspect plan/result, manifest, trusted origin, lock, interpreter prefix, Compose health and SQL-proven `tmp`; run an import/version probe through persisted Python; repeat the identical init to prove validate/refresh/resume without recreation; clean only dedicated resources | Implementer adds focused unit/integration tests and extends the existing Windows prepare job. Assertions: manifest action precedes preparation; concrete interpreter precedes Compose/bootstrap; each input occurs once; venv is valid; origin/manifest/lock agree; actual Odoo bootstrap uses selected interpreter; retry preserves venv identity and succeeds. Tester only inspects these tests/CI and reports findings | Multica-only sanitized commands, exit codes, selected plan/result excerpts, product/test revisions, interpreter/origin/lock assertions, Compose/SQL observations, MR/commit/file links, and actual candidate text. Attach `myl505-init-help-before.png`, `myl505-init-help-after.png`, `myl505-init-result.png` plus matching text files |
| CI-01 | Omitted venv; missing Python; changed selector; no-op/repair/overwrite; create/validate/compile/sync/publication/Compose/bootstrap failures; damaged venv; empty inputs; dry-run; Rich/JSON/TOON; Windows/POSIX | Lower-level tests are sufficient because each outcome is deterministic at a captured boundary and does not need a live Odoo service | No additional local script | Independently inspect scenario-to-test mapping, run the documented focused commands against the candidate, and review CI without editing Implementer-owned tests or fixtures | Existing unit/integration, CLI characterization, security/process inventory, compatibility, Windows prepare, typing, formatting, packaging, and real-Odoo jobs; no new framework. Every failure must stop later steps and preserve exactly the specified durable state | Product commit/PR/file and CI links in Multica; sanitized command/exit summaries only. Raw logs, diagnostics, hashes, and runtime JSON stay out of Git |

## Local E2E script and fixture contract

- The script SHALL accept all environment-specific values as arguments or documented environment inputs: product checkout/revision, test-repository checkout/revision, dedicated `odcli-N`, Python selector, venv path, Compose image, loopback port, XDG/catalog roots, locale, terminal width, and artifact directory. It SHALL contain no private default URL, hostname, credential, or personal identity.
- Setup SHALL verify clean dedicated checkouts and allocate only the named slot/resources. Fixtures SHALL provide one addon requirement plus one explicit requirement with a canonical duplicate so real discovery/compile/sync order can be asserted.
- The dry-run SHALL record manifest nonexistence, venv nonexistence, no dedicated Compose resources, and no catalog registration before and after. Execution SHALL record exact product and test-code revisions and distinguish them.
- The script SHALL assert the public JSON plan before inspecting internal files: `init` write/verify action, venv create/validate, optional compile/sync, origin/interpreter/lock publication, Compose, then bootstrap. It SHALL then independently inspect the persisted manifest, trusted-origin record, dependency lock, actual interpreter, imported fixture versions, Compose health, and SQL `base=installed` in `tmp`.
- The repeat run SHALL use the same venv inode/canonical root and fixture state, omit any destructive reset, and assert validation/refresh plus required lifecycle verification without `uv venv` recreation.
- Cleanup SHALL run on success or failure, stop/remove only the dedicated Compose project and database/port bindings, and remove temporary XDG/catalog roots and venv. It SHALL preserve the test MR, branch, commits, and Multica attachments. It SHALL not close/merge the MR or delete its branch without separate authority.

## Independent walkthrough and visual evidence

- Baseline is updated `main` at the approved base; candidate is the exact implementation SHA. Use equivalent checkout state, fixed locale, fixed terminal width, and the same capture command for both.
- Capture actual `odcli init --help` text before and after; the baseline MUST lack `--venv` and the candidate MUST describe the opt-in path. Capture the successful candidate Rich result showing the selected venv and concrete interpreter without secrets.
- On macOS, render the actual captured text to PNG using bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`. These are text renders, not terminal-window screenshots. Preserve the tested output language; agent-authored explanations remain English. Record any ANSI, TTY, wrapping, or progress limitation honestly.
- Missing baseline/candidate text, any required PNG, or attachment read-back prevents complete verification. Decorative images and invented output are prohibited.

## Evidence publication

- Test scripts and necessary fixtures SHALL be ordinary commits in the dedicated private test-repository MR. If the verified utility genuinely creates a functional commit, retain that commit unchanged and link it separately; otherwise do not manufacture a second commit.
- Tester SHALL publish one final Multica comment containing exact product SHA, test-code SHA, baseline SHA, sanitized commands and exit codes, assertions, provenance, product/test MR/commit/file links, and image attachments, then read back the comment/attachments.
- Images, raw output/logs, diagnostic JSON, hashes, runtime evidence, and assertion results SHALL exist only in Multica. JSON belongs in Git only when it is an actual output of the functionality under test.
