## Delivery contract

- Task key: `MYL-479`
- OpenSpec change: `add-gitlab-member-auth-workflows`
- Planning base: `origin/main` at `9e388b8f1d7ac1d1907922e07493a267007f8cfb`
- Authoritative scope: `proposal.md`, `design.md`, `tasks.md`, and all delta specs in this change directory, resolved against current main specs.
- Estimate source: the planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numeric totals are intentionally not duplicated here.
- Confidence/calibration: medium confidence and uncalibrated for agent execution. Acceptance is clear and close local analogues exist, but recursive cross-host submodules, verified GitLab commit identity, provider upload idempotency, and uv receipt variants create coupled uncertainty. No comparable agent-run history was supplied.
- Production code is implemented only after independent plan verification. Planning does not run tests or live E2E.

## Estimate evidence

The exact planning estimate and scenario bounds are stored only in the issue properties named above. The estimate measures remaining active developer-hours for one experienced developer familiar with Python, Click, Git, GitLab, and uv-tool workflows, without AI acceleration; unattended CI/provider waits and human approval time are excluded.

Evidence used: the existing `GitResource` and `internal/git_sync` state machines; `odcli-multica` credential parser, askpass, Git execution, and GitLab MR transport; the self-update provenance/journal/recovery coordinator; existing integration tests for Git lease/conflict behavior; packaging self-update tests; `PUBLIC_LEAF_CASES`; architecture/security inventories; and the current GitHub CI jobs. Shared model, credential, and test-fixture work is counted once. Tests were not run for estimation. The largest uncertainty is the security-preserving extraction of extension code into core while retaining compatibility, followed by real recursive-submodule and uv metadata variants.

## Work-package topology

`delivery_mode=dag`. Stages are consecutive topological layers; Manager SHALL write the listed stage into each WP child issue. Only direct dependencies are listed.

### WP01 — Core credential and repository foundation

- `stage: 1`
- `depends_on: []`
- Task coverage: `1.1`, `1.2`, `1.3`, `1.4`.
- Deliverable: frozen public identities/results and one core owner-only member/host credential, GitLab `/user`, askpass/redaction, and explicit repository-resolution contract, with `odcli-multica` compatibility seams defined.
- Owned responsibility scope: core credential/repository models and internal policy modules; extracted shared policy code; focused unit tests and extension adapters needed for these contracts. Critical shared files include `src/odoo_instance_sdk/models/**`, new core credential/GitLab policy modules, and the corresponding exports; later WPs consume but do not redefine these types.
- Contract surface: exact mapping schema/path, normalized host/project/repository snapshot, private credential handle, verified GitLab identity, stable typed errors, and compatibility imports.
- DoD/evidence: owner-only/malformed/ambiguous matrices, login and commit-identity verification, concurrency and secret corpus redaction, repository/Odoo classification, type/lint checks for owned paths.
- Parallel safety: foundation is intentionally exclusive; no sibling runs in stage 1.

### WP02 — Generic clone, commit, and sync

- `stage: 2`
- `depends_on: [WP01]`
- Task coverage: `2.1`, `2.2`, `2.3`, `2.4`.
- Deliverable: public command builders/resources for generic clone, safe recursive retry, explicit repository commit, and HTTPS sync using WP01 contracts.
- Owned responsibility scope: `src/odoo_instance_sdk/resources/git.py`, `internal/git_sync.py`, new focused generic Git/submodule modules, Git result extensions, and directly related unit/integration fixtures/tests. It does not register CLI leaves or implement GitLab MR HTTP actions.
- Contract surface: typed builder signatures and results consumed by WP04; immutable step IDs; generic/Odoo policy selector; parent-gitlink non-mutation guarantee.
- DoD/evidence: absent/existing clone, cross-host/hostile submodule, staged-only commit identity/format, safe branch/lease/conflict/stale-origin sync, dry-run/no-effect, and parent gitlink tests pass.
- Parallel safety: runs beside WP03; its write zone is Git resources/process fixtures, while WP03 owns self-update/package files.

### WP03 — Self-update installation composition

- `stage: 2`
- `depends_on: [WP01]`
- Task coverage: `4.1`, `4.2`, `4.3`.
- Deliverable: uv-tool provenance/snapshot/install/rollback preservation of exact extras plus new-interpreter dashboard assets/import verification and isolated packaging regression.
- Owned responsibility scope: `internal/self_update*.py`, update models/tests, `tests/packaging/test_self_update.py`, package/UI artifact assertions, and only directly related packaging fixtures. It does not edit generic Git/MR/CLI modules.
- Contract surface: normalized installed requirement/extra-set fields, install/recovery argv builder, maintenance verification result used by final integration.
- DoD/evidence: core-only and dashboard compositions, ambiguous metadata preflight, rollback/recovery parity, built SPA presence, restarted headless monitor `/healthz` and OpenAPI regression tests.
- Parallel safety: independent of WP02 after consuming only WP01 stable types; no overlapping critical write zone.

### WP04 — Merge request publication and CLI integration

- `stage: 3`
- `depends_on: [WP02]`
- Task coverage: `3.1`, `3.2`, `3.3`, `5.1`.
- Deliverable: core exact-repository MR create/update with idempotent project-upload image, extension compatibility, and complete public CLI/output registration for all new Git operations.
- Owned responsibility scope: core GitLab HTTP/action implementation, `commands/git.py` and any focused MR command module, CLI registrations/output cases, extension client/CLI compatibility adapters, and their focused tests. WP01 types and WP02 builders are consumed unchanged except for reviewed integration fixes.
- Contract surface: final CLI signatures/help, SDK `*_command()` methods, MR/image digest marker format, output envelope fields, extension compatibility API.
- DoD/evidence: unique/ambiguous MR behavior, same/changed image sequencing, orphan-upload diagnostic, no image in Git, help/usage/dry-run/format parity, and package extension suite pass.
- Parallel safety: waits for WP02 so only one WP owns Git CLI registration and resource integration at a time; WP03 may already be complete before this stage opens.

### WP05 — Documentation, verification assets, and whole-change gates

- `stage: 4`
- `depends_on: [WP03, WP04]`
- Task coverage: `5.2`, `5.3`, `6.1`, `6.2`, `6.3`, `6.4`, `6.5`.
- Deliverable: accurate README/inventory, final architecture/security contracts, reusable parameterized E2E scripts, complete cross-feature tests, and all repository verification gates.
- Owned responsibility scope: README and documentation inventories, architecture/security inventory fixtures, cross-cutting test matrices, `scripts/e2e_gitlab_member_workflow.py`, `scripts/e2e_update_dashboard.py`, and directly related fixtures. It may repair integration defects in predecessor-owned code only with coordination and records those files as shared during the repair.
- Contract surface: script CLIs/private-parameter names, CI job assertions, README inventory markers, sanitized evidence schema described in this plan.
- DoD/evidence: all affected scenarios mapped, focused and whole offline suites, extension, dashboard/package, lint, types, architecture/security checks pass; exact commands/exits recorded. Live scripts are implemented but not executed by Implementer unless the later implementation issue explicitly assigns live verification; independent Tester executes the E2E contract.
- Parallel safety: final convergence WP starts after both product fronts, so cross-cutting docs/tests cannot conflict with active siblings.

Stage mapping: `WP01 -> 1`, `WP02 -> 2`, `WP03 -> 2`, `WP04 -> 3`, `WP05 -> 4`.

Task coverage audit: `1.1–1.4` appear only in WP01; `2.1–2.4` only in WP02; `3.1–3.3` only in WP04; `4.1–4.3` only in WP03; `5.1` only in WP04; `5.2–5.3` and `6.1–6.5` only in WP05. Stage 2 is a real execution frontier with two independent WPs.

## E2E decision

`e2e_required=true`.

Real GitLab and isolated uv-tool E2E are required because fake HTTP/process tests cannot prove token-owner identity, askpass propagation through recursive submodules, provider-side MR upload/idempotency, retained branches/commits/MRs, or a restarted installed monitor. At execution time, Tester SHALL read the private test repository URL from the current Multica project description and pass it to scripts as `TEST_REPOSITORY_URL`; the URL/hostname SHALL NOT be copied into this repository, command templates, or other public artifacts. Scripts and public Git artifacts SHALL use parameter names or repository-relative identities and SHALL NOT commit private hostnames, member UUIDs, logins, tokens, or personal data to GitHub.

Required private prerequisites are one authorized Multica member UUID and its exact-host token mapping in the owner-only user credential store, plus five GitLab repository roles: `A`, superproject `B`, submodule repositories `B/ours` and `B/odoo`, and an independent repository. Tester reads the URL for `A` from the current Multica project description and receives the other four URLs and the selected member ID through private, non-echoed runtime inputs. Tester verifies read and write permission for the intended test branches before mutation, and checks that `B` points to the two supplied submodule repositories. Missing bindings, topology, or permission block live verification; no fake coverage is substituted. A second live GitLab user and token are not required in this E2E run. Cross-member isolation, wrong-owner rejection, and two-member mapping behavior remain mandatory offline regressions; live two-user verification is deferred until that infrastructure exists.

## Verification matrix

| Case | OpenSpec requirements/scenarios | Verification gap / local E2E decision | OpenAPI | Script | Independent Tester steps | CI disposition | Assertions and evidence |
|---|---|---|---|---|---|---|---|
| AUTH-LOWER | Protected pre-clone files: resolve, permissions, ambient identity; token owner: valid, mismatch, missing fields; secret delivery: concurrency, echo | Lower-level checks are sufficient for parsers, permission gates, mismatch-before-effect, projection redaction, and concurrent snapshots; no live secret-negative E2E that risks disclosure | N/A | none | Inspect owner-only modes and sanitized plan/result from disposable fake credentials; do not print token values | Extend `offline-tests`: `tests/unit` and `packages/odcli-multica/tests` mapping/HTTP/process fakes | Exit codes, zero-effect ledgers, sanitized stdout/stderr; no evidence file contains token bytes |
| GIT-CLONE | Generic clone: standalone, retry, wrong origin, unapproved submodule; recursive credential boundary scenarios | Live GitLab needed for real askpass and recursive child propagation; cheaper fakes cannot prove Git subprocess behavior | N/A | `scripts/e2e_gitlab_member_workflow.py clone` | In a clean Tester checkout, clone A and recursive B, switch B to a retained feature branch, deinitialize one submodule, rerun clone, inspect branch/status/submodule SHAs | Offline fixtures cover wrong-origin/host/helper/hook/protocol failures; live case is local-only because private credentials are unavailable to GitHub CI | Exit `0`; credential-free origins; retry preserves branch/dirty-free state; only missing submodule initialized; command/exit text attached in Multica |
| GIT-COMMIT | Generic commit scenarios; explicit repository Odoo boundary scenarios; identity-missing negative | Live GitLab identity and actual commit metadata required; formatting/policy branches also get unit coverage | N/A | `scripts/e2e_gitlab_member_workflow.py commit` | Stage one deterministic fixture change in A, B/ours, and independent repo; commit as the same selected member in each; inspect `git show --format=fuller`; inspect A policy vs generic policy | Extend `offline-tests` unit/integration matrices; live identity is local-only | Verified author/committer, expected subject rules, exact staged paths, no auto-stage; retain genuine created commit SHAs and links in Multica |
| GIT-SYNC | Sync independent branch, stale remote, submodule checkout; remote-after-plan | Live fetch/rebase/push and parent gitlink observation required | N/A | `scripts/e2e_gitlab_member_workflow.py sync` | Record B gitlink before/after, publish A and B/ours feature branches, create a controlled stale-lease race in the independent repo, inspect remote refs | Offline fake executor covers transitions; live remote case local-only | Same-name branches, expected lease rejection with no retry, B superproject index/HEAD unchanged; MR/commit/ref links in Multica |
| MR-LIVE | Core MR create/update/ambiguity; image first/repeat/failure | Real project uploads and MR provider state are the remaining gap | N/A | `scripts/e2e_gitlab_member_workflow.py mr` | Create/update two MRs for A and B/ours using UTF-8 files; upload one PNG, repeat unchanged, then update image; inspect description and Git tree | Extend `offline-tests` fake GitLab request sequencing; live upload local-only | Correct project/source/target; one owned marker/link; no second upload for same digest; image absent from Git; retained MR URLs attached |
| PLAN-LOWER | Generic GitLab immutable plans; repository-change revalidation; recursive fail-closed boundary | Existing process/action ledger is deterministic and fully testable offline | N/A | none | Compare dry-run JSON to executed scenario identities and inspect that no secrets/private helper paths appear | Extend `offline-tests`, architecture and CLI output contracts | Ordered step IDs, no dry-run effects, stale local inputs rejected, process inventory remains single-boundary |
| UPDATE-LIVE | Preserve dashboard/core extras; ambiguous provenance; dashboard verification; packaging update scenario | Real uv-tool replacement and restarted imports/assets/API are not proven by unit tests | `GET /healthz` response `{"status":"ok"}` and generated OpenAPI availability | `scripts/e2e_update_dashboard.py` | Create isolated supported `[dashboard]` uv-tool install at baseline SHA, update to candidate SHA, restart headless monitor on allocated port, request `/healthz` and `/openapi.json`; repeat core-only composition check | Extend `package`/`dashboard-tests` for built assets and isolated install; live revision replacement remains focused local E2E | Installed requirement retains exact extras, imports succeed, SPA asset exists, health `200`, OpenAPI parses; exact revisions/commands/exits attached |
| CLI-DOC | CLI surface/help/missing context; README inventory | Contract tests prove inventory; visible new output requires capture | N/A | reuse focused public commands, not a duplicate validator | Run baseline and candidate `git --help`, clone dry-run, commit dry-run, sync dry-run, MR dry-run at fixed width/locale | Extend `offline-tests` CLI inventory and `PUBLIC_LEAF_CASES` | Help paths/options and machine envelopes match specs; no project resolution for help; before/after text and PNG renders attached |
| EXT-COMPAT | Existing `odcli-multica` Git/credential/MR requirements unchanged | Compatibility is fully observable in existing package tests; no duplicate live walkthrough | N/A | none | Inspect package imports and run its focused suite; report findings | Existing `offline-tests` package command plus extended compatibility tests | No core Multica dependency; existing extension API/CLI tests pass |
| README | README exact command inventory and boundaries | Static contract sufficient; no live E2E | N/A | none | Compare documented examples to `--help`; inspect no private URL/data entered Git | Extend README/Click inventory test in `offline-tests` | Every registered public path once; explicit exclusions accurate |

All remaining spec scenarios are covered by the grouped requirement rows above: every negative-before-effect branch belongs to `AUTH-LOWER`, `PLAN-LOWER`, or the offline part of its command row; no scenario is excluded.

## Script and fixture contract

The Implementer owns tasks 6.3 and 6.4 and commits the reusable scripts, necessary public fixtures, and assertions. The scripts SHALL accept private values only through named environment variables or non-echoed file inputs; record product SHA separately from script/evidence SHA; emit bounded sanitized summaries; and implement `try/finally` local cleanup. They SHALL reuse Python, `internal/proc`-compatible public CLI calls, existing test helpers, and the repository test runner—no second runner or validator framework.

`scripts/e2e_gitlab_member_workflow.py` SHALL accept one selected member and the five repository roles through private inputs, clone each role actually used by a case, and assert the recursive `B` topology rather than cloning one URL twice. It SHALL provide setup/reset for disposable local checkouts and deterministic fixture edits, but SHALL not create hidden provider projects or assume personal paths. It SHALL preserve the feature branches in A and B/ours, genuine functional commits, uploaded project files, and two MRs for review. Cleanup removes local clones/credential staging only; it SHALL not close/merge MRs or delete remote branches/commits.

`scripts/e2e_update_dashboard.py` SHALL use an isolated user root/tool directory, candidate and baseline SHAs, an allocated port, bounded readiness timeout, `/healthz`, and `/openapi.json`. Cleanup stops only its recorded monitor PID after comparing it with the Multica daemon PID, then removes its isolated local root. Build products, raw logs, and environment dumps are not committed.

The Tester SHALL execute these scripts from a fresh checkout after implementation and SHALL NOT edit tests, fixtures, scripts, or product code. Any missing coverage is a finding returned to the Implementer. Odoo runtime execution is not required for these Git contracts; if Tester elects to validate A with Odoo, it SHALL use a dedicated `odcli-N`, own checkout/database/port, and never the reference checkout.

## Independent walkthrough and images

Tester SHALL perform public CLI actions manually in addition to script execution: inspect credential-free remotes, current branches/submodule status, `git show --format=fuller`, parent gitlink status, GitLab MR descriptions/uploads, installed uv requirement metadata, and the restarted monitor API. Re-running the script alone is not independent inspection.

Because help, dry-run tables, errors, and update summaries change, Mac verification SHALL capture actual UTF-8 text for baseline and candidate with `LC_ALL=C`, fixed 120-column width, equivalent sanitized fixtures, and explicit revision labels. Render that captured text to PNG using the bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`; label outputs as renders, not terminal screenshots. Required pairs are:

- `cli-git-help-before.txt/png` and `cli-git-help-after.txt/png`
- `git-clone-dry-run-before.txt/png` (baseline unsupported-command output) and `git-clone-dry-run-after.txt/png`
- `git-sync-selected-member-before.txt/png` and `git-sync-selected-member-after.txt/png`
- `git-mr-image-repeat-before.txt/png` and `git-mr-image-repeat-after.txt/png`
- `update-dashboard-before.txt/png` (reproduced dependency-loss failure) and `update-dashboard-after.txt/png` (successful preserved-extra update/restart summary)

If ANSI, TTY prompts, or progress cannot be preserved by redirection, the evidence SHALL state that limitation and capture the stable final output; it SHALL not claim interactive-state coverage. Missing a mandatory baseline/candidate capture blocks complete verification.

## Evidence publication and cleanup

The final Tester comment in Multica SHALL attach all PNGs and relevant bounded text/log excerpts and link the GitLab MRs, branches, genuine commits, and changed files. It SHALL state candidate product SHA, script/evidence SHA, commands, exit codes, assertion results, image provenance, and cleanup result, then read back the comment/attachments. Actual logs, diagnostic JSON, hashes, secret-scan results, screenshots/renders, and provenance stay in Multica only. GitHub receives only implementation, tests/fixtures/scripts, generated code/docs that are product outputs, and genuine code commits; private URLs/hostnames, personal identifiers, credentials, raw evidence, and generated diagnostic JSON SHALL NOT be committed.

Functional-result commits created by the verified Git utility remain unchanged and are linked as evidence. Tests and applicable functional results are kept in distinct commits where both exist; a second commit is not invented when no separate functional result exists. Routine cleanup removes local stands and isolated credentials while preserving evidence MRs/branches/commits. Merge, MR close, and remote branch deletion require separate authorization.
