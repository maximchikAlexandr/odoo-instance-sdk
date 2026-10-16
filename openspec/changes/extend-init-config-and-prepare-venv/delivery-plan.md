# Delivery plan

## Planning baseline

- Task key: `MYL-491`.
- OpenSpec change: `extend-init-config-and-prepare-venv`.
- Approved implementation base: `origin/main` at `7e29e50a0de123565c46f9f566ab67cca0963955`.
- Test repository: use the URL from the current Multica project description at execution time; do not copy private hostnames into public Git content.
- Current-main audit: existing generated-config creation/repair, Compose bootstrap ordering, uv checkout/sync mechanics, worktree rebasing, locks, and CI fixtures are retained and extended rather than rebuilt.

## Estimate contract

- Authoritative totals exist only in planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; they are intentionally not duplicated here.
- Basis: remaining work from the recorded base to all change acceptance criteria for one experienced developer familiar with Python, Click, and uv, without AI acceleration. Active hours include investigation, implementation, required tests, likely review fixes, documentation, and attended verification; unattended CI/service waits and approval queues are excluded.
- Confidence: medium. The affected paths and close analogues were inspected and most lifecycle machinery exists, while primary-venv persistence, recursive/canonical discovery, and broad CLI characterization remain bounded integration risks.
- Calibration: uncalibrated because no comparable executor timing history was supplied. Tests were not run for the estimate.
- Evidence: `ProjectConfig`; public/internal project-init; `internal/generated_config.py`; checkout planning/artifacts/settings; dependency sync; focused init/environment/CLI tests; real-Odoo CI; the complete R2 OpenSpec package.
- Scope reconciliation: existing config creation, repair, secret handling, bootstrap ordering, uv execution, managed ownership/hash-lock policy, and process boundaries are already satisfied foundations and are not re-estimated as new subsystems.

## Delivery topology

The authoritative headline estimate property selects `single_wp_no_dag`. Delivery therefore has exactly one all-covering implementation work package. It has no `depends_on`, no stage, no execution frontier, and no DAG orchestration. Manager SHALL materialize no WP child until the R2 plan passes independent verification.

### WP-01 — Implement and verify the complete change

- Delivery mode: `single_wp_no_dag`.
- Task coverage: all tasks `1.1`–`4.3`, each covered exactly once.
- Independent deliverable: a complete SDK-first implementation that stores and renders ordered project inputs, shares recursive dependency discovery, prepares the primary checkout venv, exposes the CLI surfaces, and passes required repository and E2E verification.
- Owned responsibility scope: the full change across `ProjectConfig`, public/internal init, generated config, managed dependency planning/sync adapters, the narrow primary-venv public operation, Click registration and canonical inventories, directly related tests/fixtures/snapshots/docs/CI files, and the test-repository script. Scope is responsibility-based, not an exhaustive file allowlist.
- Contract surface: optional ordered manifest tuples; omitted-versus-supplied init behavior; existing config overlay/repair and security; deterministic canonical discovery; managed hash-lock/ownership preservation; immutable primary-venv plan and post-success manifest persistence; transport-only Rich/JSON/TOON CLI.
- Definition of done / evidence: every delta scenario maps to a focused passing test or the selected local E2E case; existing real-Odoo and repository gates pass; no new resolver, settings, runner, catalog, dynamic-process, or init-transaction subsystem appears; exact product/test SHAs, commands, exit codes, assertions, links, and required images are published in Multica.
- Parallel-safety rationale: not applicable. The threshold contract requires one WP, and the remaining public/model/CLI/verification edits share enough cross-cutting contracts that splitting ownership would add coordination without changing delivery acceptance.

## E2E decision and verification contract

`e2e_required=true`. Unit and integration tests can prove parsing, rendering, traversal, argv, locks, redaction, failure, and mutation boundaries. A local E2E remains necessary to prove the new stored config values and shared inputs against the private test-repository layout and to exercise a real uv-created/refreshed primary venv. The existing GitHub real-Odoo smoke remains the Odoo consumption boundary and SHALL be extended by the Implementer where required; Tester SHALL inspect it but not edit product CI fixtures.

Planning does not execute tests. Tester SHALL use a dedicated `odcli-N` slot with its own checkout, database when required, port, XDG/catalog roots, and venv; the reference checkout SHALL remain untouched.

| Case ID | Requirement coverage | Verification path | Independent Tester actions | Assertions | Evidence |
| --- | --- | --- | --- | --- | --- |
| E2E-01 | Stored inputs; generated config overlay/repair; shared discovery; primary venv; CLI output | One committed `scripts/myl491_init_prepare_venv_e2e.py` plus ordinary fixtures in the test repository | Capture baseline help/error; create nested addon modules with module/parent requirements, Odoo-core and duplicate explicit inputs; run init and dry-run prepare; execute real prepare into a fresh slot venv; verify config, interpreter, imports, manifest, and canonical order; change one fixture pin and refresh the existing venv; repeat init to exercise no-op/repair; perform safe cleanup after capture | Stored and rendered order matches; config remains mode `0600`; source custom option remains; each dependency input occurs once; dry-run mutates nothing; interpreter belongs to selected venv; imports/versions match joint resolution; refresh reuses venv; manifest changes only after success | Sanitized commands/exit codes, actual before/after text, config excerpts, interpreter/package assertions, product/test MR/commit/file links, and `myl491-before.png` / `myl491-after.png` attached to Multica |
| CI-01 | All deterministic, failure, compatibility, and real-Odoo scenarios | Existing GitHub unit/integration, real-Odoo, CLI characterization, security/process, typing, formatting, and packaging jobs | Inspect focused test-to-scenario mapping and CI result; report findings without editing Implementer-owned tests | Old manifests remain compatible; external DB/hash-lock/ownership behavior is unchanged; failure/dry-run atomicity and POSIX/Windows projections pass; real Odoo consumes the generated values | Product commit/MR/file links and CI links in Multica; no copied CI logs or diagnostics in Git |

### Test-repository and evidence rules

- The script and necessary fixtures SHALL be committed to a dedicated test-repository branch/MR. Test-code commits SHALL remain separate from any functional commit genuinely created by the utility under test; if the verified function creates no commit, no synthetic second commit is required.
- Tester SHALL record candidate product SHA, test-repository test SHA, baseline SHA, exact commands, exit codes, assertions, sanitized provenance, and MR/commit/file links in the final Multica comment with attachment read-back.
- Runtime logs, diagnostic JSON, hashes, screenshots, and raw command output SHALL be published only in Multica, not committed to GitLab, unless a JSON file is itself the functional product output under test.
- On macOS, changed visible output SHALL have actual baseline/candidate text and PNGs rendered through bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`. Captures retain the tested output language; plan and agent-authored explanations remain English. ANSI/TTY/progress limitations SHALL be stated honestly.
- Baseline and candidate use equivalent fixtures, fixed locale and terminal width, reset state, and the same assertion path. Redirected text does not prove TTY/ANSI behavior.
- Cleanup SHALL stop processes and remove the dedicated slot database, port binding, temporary XDG/catalog roots, and venv while preserving the evidence MR, branch, commits, and Multica attachments. The MR SHALL not be closed or merged and its branch SHALL not be deleted without separate authority.
