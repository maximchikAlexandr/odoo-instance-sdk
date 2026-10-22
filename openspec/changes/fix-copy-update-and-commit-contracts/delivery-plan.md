# Delivery Plan

## Contract baseline

- Task key: `MYL-546`
- OpenSpec change: `fix-copy-update-and-commit-contracts`
- Approved base: `origin/main` at the exact SHA recorded in the planning publication comment.
- Requirements source: GitHub #169 plus this change's proposal, design, five delta specifications, and tasks.
- Estimate source of truth: the planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`. Numerical totals are intentionally not duplicated here.
- Implementation MUST preserve the task IDs in `tasks.md`; status and assignee remain issue-system state, not OpenSpec content.

## E2E decision

`e2e_required=true` for independent post-implementation verification, while no E2E is executed during planning. The issue does not require a broad product sweep, but two material gaps remain after lower-level tests: the COPY defect depends on real Odoo server-side archive/filestore behavior, and coordinated self-update depends on isolated uv-tool environments and actual distribution provenance. A focused project-aware commit walkthrough is also required to prove the local commit object carries the previewed message and human identity. These checks implement the issue's separately performed live verification without expanding implementation scope.

The test repository is supplied by the Multica project description and SHALL be passed to tooling as `${ODOO_E2E_REPOSITORY_URL}`; its private hostname SHALL NOT be committed to public Git. Real Odoo verification SHALL use a fresh numbered `odcli-N` checkout with its own worktree, database, HTTP port, user root, logs, and cleanup. The Tester SHALL create it through the test repository's `scripts/agent_checkout.py create` with both the numbered project profile and reference checkout configured by the environment, plus `--sdk-sha <candidate-sha>`, a free port, and a unique target database. The reference checkout and existing non-numbered Odoo/PostgreSQL services SHALL remain untouched.

## Verification matrix

| Case | OpenSpec requirement/scenario | Verification gap / local decision | OpenAPI operation/schema | Local script or command | Independent Tester steps | CI disposition | Assertions | Artifacts / images |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `E2E-MYL546-01` | `development-environment` / ordinary COPY, adoption, missing file, no file-backed attachments, running primary | Required: only a real Database Manager backup/restore proves which server-side `data_dir` receives files. | None | Extend `tests/integration/real_odoo/test_critical_path.py` and run the focused `real_odoo` selection through the fresh numbered slot. | Seed the existing probe attachment; keep the primary running; execute ordinary COPY and one adoption path; inspect source and target file bytes and catalogue state; repeat with a deliberately missing target file and a database with no file-backed attachments; remove the slot through the wrapper. | Implementer extends the existing real-Odoo full job/fixture; Tester does not edit it and verifies the job selection covers the new assertions. | Target file exists under the captured target database directory; XML-RPC attachment bytes match; source bytes/hash remain unchanged; helper port differs from primary; missing file never reaches `READY`; empty file-backed set succeeds; borrowed backup remains. | Multica-only sanitized commands, exit codes, source/target relative paths, hashes, catalogue state, candidate SHA, test commit/MR links. No image because visible output is not changed by this case. |
| `E2E-MYL546-02` | `odoo-git-workflow`, `cli-odcli`, `multica-user-credentials` / bare URL, trailer, project-aware verified identity, stale/unverified refusal | Required focused workflow: unit tests cannot prove the resulting Git commit object and root-creator-to-author chain together. | None | A committed SDK integration test under the existing Git/Multica suites plus public CLI dry-run/execute commands in a disposable test-repository branch. | From verified task context call `client.context()` then `root_creator()`; stage a deterministic module change; run baseline and candidate dry-run with fixed locale/width; execute candidate commit; inspect `git show -s --format=raw`; exercise one invalid lineage or unmapped member without committing. | Extend existing unit/component Git and `packages/odcli-multica/tests` coverage; private credentialed walkthrough remains local-only because CI has no human token fixture. | Exact subject; bare URL second paragraph; preserved `Co-authored-by`; author and committer equal verified GitLab name/email; dry-run has no token; failure path creates no commit and does not use ambient identity. | Preserve the real candidate commit as the functional result and link its branch/MR. Publish sanitized raw commit text, commands/exit codes, identity provenance IDs, and baseline/candidate text plus PNG renders in Multica only. |
| `E2E-MYL546-03` | `self-update` / core-only, coordinated update, warning, embedded SDK mismatch, partial recovery | Required: isolated uv-tool environments and launchers are needed to prove the companion embeds the target SDK revision and that partial mutation is recoverable. | None | Extend `tests/packaging/test_self_update.py`; run the focused packaging selection in isolated `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` with local VCS URL rewriting already used by that suite. | Create baseline core and companion uv tools from an older fixture revision; run candidate `odcli update --ref <candidate-sha> --yes`; invoke both launchers; inspect PEP 610/uv receipt revisions in both environments; repeat a controlled companion verification failure and inspect journal/snapshot; run a separate core-only fixture. | Implementer extends the existing packaging job, fixtures, and timeouts; no new CI framework. | Both launchers run; core, companion, and companion-embedded SDK report candidate VCS revision; core-only remains companion-free; pre-mutation output names both tools; mismatch/partial failure is not `updated` and retains or successfully consumes two-tool recovery evidence. | Multica-only sanitized receipt projections, launcher paths relative to isolated root, commands/exit codes, revision assertions, journal/snapshot states, candidate SHA, baseline/candidate warning text and PNG renders. |
| `REG-MYL546-04` | All remaining scenarios and invariants | Local live execution adds no material evidence beyond focused lower-level checks. | None | Focused pytest suites plus lint, typing, architecture/redaction, coverage, standard repository checks, and strict OpenSpec validation. | Inspect test names/assertions and run the published commands against the candidate; do not modify tests. | Required in normal CI or repository checks. | CLI pairing and tag errors, legacy angle-link acceptance, no-attachment success, borrowed backup retention, ambiguous companion preflight, secret-free projection, immutable step parity, and recovery serialization are all asserted. | Multica-only command list, exit codes, concise failures, candidate SHA, CI/MR links. No decorative images. |

## Test assets and ownership

- Implementer owns all automated tests, fixtures, packaging helpers, translations/generated documentation required by tasks 1.3, 2.4, 3.4, 4.1, and 4.2. Tests and any independently generated functional result SHALL be separate commits when both exist; an actual Git commit produced by the tested utility is preserved under its natural SHA and linked rather than renamed for evidence.
- Tester owns only independent execution, inspection, assertions, evidence publication, and failure-safe cleanup. Tester SHALL NOT edit implementation tests or fixtures.
- No diagnostic JSON, terminal captures, logs, hashes, screenshots, approval history, private URLs, personal data, or evidence-only reports are committed. JSON belongs in Git only when its generation is itself the tested product behavior.
- The candidate product SHA, test-code SHA when different, exact command, exit status, locale, terminal width, and scenario ID accompany every evidence item.

## Image contract

Visible output changes in the Git dry-run and coordinated update warning require actual baseline/candidate text on macOS with equivalent sanitized fixtures, `LC_ALL=C`, and fixed 120-column width. Render those captured UTF-8 texts through the bundled `openspec-e2e-testing/scripts/render_terminal.swift` into:

- `E2E-MYL546-02-git-dry-run-before.png`
- `E2E-MYL546-02-git-dry-run-after.png`
- `E2E-MYL546-03-update-warning-before.png`
- `E2E-MYL546-03-update-warning-after.png`

These are renders of actual captured text, not terminal-window screenshots. Preserve the tested output language; agent-authored explanation remains English. State honestly when redirection omits ANSI color, TTY-only progress, spinners, or prompts. Missing any required text/PNG pair blocks complete Tester verification.

## Evidence publication and cleanup

- Tester posts one final Multica comment with read-back confirming attachment metadata for every required PNG and links to the evidence branch, MR, product/test commits, and any functional-result commit. Raw/sanitized text, commands, exit codes, assertions, hashes, and provenance are comment content or attachments only.
- Cleanup SHALL run on success and failure. It removes the numbered Odoo slot with the same numbered project profile, its target database, temporary uv-tool roots, local credentials, and capture staging files while retaining the evidence MR, branch, and commits.
- Cleanup SHALL NOT close or merge an MR, delete a remote evidence branch, stop Colima, stop shared/reference PostgreSQL, or modify the reference checkout without separate authorization.

## Delivery topology

`delivery_mode=dag`. The authoritative `Estimate, hours` planning-issue property is above the multi-WP threshold, and the graph has a genuine parallel frontier. Stages are consecutive topological layers; they do not encode WIP limits or live status.

| WP | Stage | OpenSpec task coverage | Direct `depends_on` | Independent deliverable |
| --- | ---: | --- | --- | --- |
| `WP-MYL-546-01-copy-filestore` | 1 | `1.1`, `1.2`, `1.3` | none | COPY and adoption restore/readiness use and prove the captured target filestore without source or primary-runtime regression. |
| `WP-MYL-546-02-core-git-identity` | 1 | `2.1`, `2.2`, `2.4` | none | Core SDK/CLI generates bare ticket links and performs project-aware verified-member commits through one immutable snapshot. |
| `WP-MYL-546-03-multica-root-creator` | 2 | `2.3` | `WP-MYL-546-02-core-git-identity` | Multica commit automation selects only the verified root human creator and consumes the core member-only project commit contract. |
| `WP-MYL-546-04-companion-update` | 1 | `3.1`, `3.2`, `3.3`, `3.4` | none | Self-update discovers, updates, verifies, and recovers an optional installed companion plus its embedded SDK while preserving core-only installs. |
| `WP-MYL-546-05-integration-publication` | 3 | `4.1`, `4.2` | `WP-MYL-546-01-copy-filestore`, `WP-MYL-546-03-multica-root-creator`, `WP-MYL-546-04-companion-update` | Cross-feature documentation, contract inventories, full repository checks, and implementation handoff evidence are complete. |

Complete task coverage is exactly once: `1.1–1.3` → WP-01; `2.1`, `2.2`, `2.4` → WP-02; `2.3` → WP-03; `3.1–3.4` → WP-04; `4.1–4.2` → WP-05. The `wp_id -> stage` contract is: `WP-MYL-546-01-copy-filestore -> 1`, `WP-MYL-546-02-core-git-identity -> 1`, `WP-MYL-546-04-companion-update -> 1`, `WP-MYL-546-03-multica-root-creator -> 2`, `WP-MYL-546-05-integration-publication -> 3`.

### WP-MYL-546-01-copy-filestore

- **Owned responsibility scope:** checkout plan/execution, auxiliary restore configuration, COPY finalization, filestore containment/postconditions, COPY/adoption tests and directly related fixtures. Critical shared files include `resources/environment/checkout*.py`, `resources/environment/settings.py`, `resources/instance/auxiliary_restore*.py`, and focused environment/real-Odoo tests.
- **Contract surface:** `development-environment` delta, existing `database-restore` and immutable command contracts, COPY journal stages, backup ownership, generated config, distinct helper-port behavior.
- **Definition of done / evidence:** all task 1 scenarios have focused unit/component coverage; source and target file assertions are real bytes rather than restore-call mocks; missing target files block `READY`; no-file-backed case succeeds; adoption and ordinary COPY agree; owned/borrowed cleanup remains correct.
- **Parallel safety:** writes only checkout/restore and their focused tests/fixtures; it does not edit Git workflow, Multica credential composition, self-update, or shared documentation owned by active siblings.

### WP-MYL-546-02-core-git-identity

- **Owned responsibility scope:** core commit message generation/validation, CLI option relationships, initialized-project host resolution, verified author/committer environment capture, Git CLI/resource tests and directly related fixtures. Critical shared files include `resources/git.py`, `commands/git.py`, `internal/git_explicit.py`, Git policy code, README examples only if WP-05 has not started, and focused Git tests.
- **Contract surface:** `odoo-git-workflow` and `cli-odcli` deltas, existing GitLab member token-owner verification, immutable command and redaction contracts.
- **Definition of done / evidence:** exact generated bare-link body; legacy angle-link plus standard trailer accepted; repo/member/tag usage matrix enforced; member-only project/environment dry-run and execution share message, staged tree, and safe identity; invalid identity fails before commit with no ambient fallback.
- **Parallel safety:** writes core Git surfaces and tests only. WP-03 waits for this contract; WP-01 and WP-04 own disjoint runtime/update code. WP-05 owns final shared docs and global checks.

### WP-MYL-546-03-multica-root-creator

- **Owned responsibility scope:** `packages/odcli-multica` context/root-creator-to-commit composition and its focused tests. Critical shared files include `odcli_multica/client.py`, `client_policies.py`, command composition entry points, and package tests.
- **Contract surface:** `multica-user-credentials` delta plus the completed WP-02 member-only project-aware commit API. The only accepted selector is the verified Multica root human creator UUID.
- **Definition of done / evidence:** valid child lineage supplies root creator to core commit; missing/cyclic/cross-workspace/non-human/drifted lineage fails before credential lookup or Git; tests exclude metadata, assignee, agent, numeric GitLab ID, and machine fallbacks.
- **Parallel safety:** after WP-02, writes only the companion package and its tests. It does not edit core Git files, checkout/restore, self-update, or final cross-feature docs.

### WP-MYL-546-04-companion-update

- **Owned responsibility scope:** uv-tool composition discovery, update models/commands, process steps, snapshot/journal/recovery, verification, self-update unit/packaging tests, and directly related fixtures. Critical shared files include `internal/self_update*.py`, update result models/CLI rendering, `tests/unit/test_self_update*.py`, and `tests/packaging/test_self_update.py`.
- **Contract surface:** `self-update` delta, existing nine-phase update, process-ledger parity, optional-extras preservation, rollback/unknown-outcome, alternate-launcher rejection, and packaging contracts.
- **Definition of done / evidence:** absent companion leaves the core plan unchanged; present companion has unambiguous protected provenance; both pinned installs and both launchers are frozen; embedded SDK revision is verified; warning precedes mutation; partial failure never reports success and retains or consumes valid two-tool recovery evidence.
- **Parallel safety:** writes only self-update code/tests. It does not edit checkout, core Git, Multica client, or final shared documentation/check orchestration.

### WP-MYL-546-05-integration-publication

- **Owned responsibility scope:** user-facing documentation, CLI/execution inventories affected across domains, complete validation commands, reconciliation fixes that span completed WP contracts, and the implementation handoff. Directly related snapshots/generated docs may be updated; production feature ownership remains with predecessor WPs unless a cross-contract integration defect requires a coordinated fix.
- **Contract surface:** all change specs, tasks 4.1–4.2, repository rules, strict OpenSpec, standard CI/local checks, and the E2E contract above.
- **Definition of done / evidence:** documentation accurately describes all shipped behaviors; focused and standard checks, lint, typing, architecture/redaction, coverage, packaging, and strict OpenSpec results are recorded with exact commands/exit codes; no planning-only evidence or secrets are committed; Tester receives exact candidate SHA and case mapping.
- **Parallel safety:** starts only after all feature branches represented by WP-01, WP-03, and WP-04 are integrated, so its shared documentation, inventory, and global-check write zones have no active sibling conflict.

## Execution frontiers

- **Stage 1 frontier:** WP-01, WP-02, and WP-04 are independent and parallel-safe.
- **Stage 2 frontier:** WP-03 begins after the WP-02 core commit contract is available.
- **Stage 3 frontier:** WP-05 begins after WP-01, WP-03, and WP-04; WP-02 is already included transitively through WP-03.

Dependencies are direct only. Shared contracts are placed in predecessor WP-02, and no parallel siblings own overlapping critical write zones.
