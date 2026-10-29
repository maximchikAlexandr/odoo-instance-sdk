## Delivery Contract

- Task key: `MYL-595`
- Change: `integrate-multica-git-credentials`
- Approved base: `f0918da2d36b59c35cf0a949efc58744d1efb7f9` (`origin/main`)
- Planning source: GitHub issue `#168`, with the Multica issue instruction taking precedence over its obsolete deferral sentence.
- Implementation ownership: Implementer owns product code, tests, fixtures, scripts, generated package metadata, and documentation. Tester performs independent verification and does not edit implementation or CI coverage.
- Estimate source of truth: the planning issue custom properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`. Numeric totals SHALL NOT be copied into this file, comments, implementation issues, or the visual explanation.

## E2E Decision

`e2e_required=true`.

Lower-level tests can prove parsing, precedence, redaction, typed selection, and command construction, but they cannot prove the remaining cross-component gap: one real native Multica daemon must use the configured read-only identity for cold-cache and later background fetch while two verified task checkouts of the same real repository concurrently use different member identities without fallback or global-account mutation. Live verification is also required to capture the changed safe CLI identity output. The test repository is supplied from the active project's description through `TEST_REPOSITORY_URL`; its private URL/hostname SHALL NOT be written to Git.

No Odoo server, database, filestore, numbered `odcli-N` slot, or browser UI is required because the affected public boundaries are Multica repository checkout, Git-over-HTTPS, GitLab HTTP, and `odcli-multica` CLI output.

## Verification Matrix

| Case | OpenSpec requirement/scenarios | Gap / local E2E decision | OpenAPI | Repeatable script | Independent Tester actions | CI disposition | Assertions | Evidence and images |
|---|---|---|---|---|---|---|---|---|
| C1 | `gitlab-member-credentials`: protected files; `multica-user-credentials`: host mapping | No live gap; filesystem and encrypted-store precedence are deterministic | N/A | None | Inspect owner-only fixtures and invoke dry-run once with user-root-only data | Existing offline jobs; extend core and bridge unit tests | Encrypted-first; explicit-path isolation; process/user/compatibility order; `ODCLI_USER_ROOT`; no project copy | CI links and selected test names; no image |
| C2 | Stable helper entrypoint; runtime token-owner verification | Installed entrypoint and Git protocol need packaged-process proof; provider scope variants remain mocked | N/A | Existing package smoke tests | Install wheel in an empty environment; invoke `git credential fill` with process-local helper config and fake bounded `/user` plus self-information responses | Existing `package` and compatibility jobs; extend `tests/packaging/test_artifacts.py` and helper unit tests | Wheel/sdist/numbered install exposes executable; only `get` succeeds; configured login and runtime provider ID agree with self-information; host/purpose/member exact; no stored provider-ID field, Git config secret, or URL secret | Package job/MR links, command/exit status, sanitized stdout/stderr; no image |
| C3 | `multica-git-cache-credentials`: cold cache, background fetch, process-local config, rollback | **Local E2E required**: native daemon/cache and remote Git interaction cannot be represented by lower-level tests | N/A | Extend `scripts/e2e_gitlab_member_workflow.py` with a cache phase | With an isolated profile and empty cache, validate configured login/runtime provider ID against token self-information, start one daemon generation with process-local helper config, request checkout, add a remote commit through a separate fixture identity, trigger a later supported fetch, inspect remote/config/process state, stop daemon and its Git children, remove helper inputs, start a rollback generation, and confirm the helper is no longer used | Unit/integration tests cover owner/self-information comparison, config projection, and broader-scope refusal; live daemon remains local-only because CI has no private credentials | Exact candidate SHA; runtime owner ID equals active token self-information owner with exact `read_repository`; cold and later fetch succeed; one generation keeps immutable environment; no task member consulted; remote/config contain no token/member; rollback generation has no replacement/helper environment | Sanitized phase summary, cache before/after object SHA facts, daemon generation/process facts, commands/exits, cleanup ledger; no visible-output image |
| C4 | Shared task-member selection; binding override; cache/task separation | **Local E2E required**: concurrent real checkout isolation and automatic-versus-explicit selection cross Multica, Git, and credential transport | N/A | Same script, task-routing phase | Create two isolated root tasks/checkouts for the same repository with different human members; run one automatic root-creator fetch and one explicit `--member-id` fetch/sync concurrently; manually inspect each safe identity result and repository state rather than rerunning the script | Existing `offline-tests` bridge suite; extend `test_credentials.py`, `test_git_transport.py`, `test_cli.py`, and `test_output_contract.py` | Each operation reports its own member/source and receives only its token; context/repo/host stay fixed; cache member unchanged; invalid/non-human/cross-workspace member fails before Git | Sanitized command/exits, task/member aliases (not personal data), result assertions, no secret corpus match; before/after text and PNG for automatic and explicit output |
| C5 | Negative cache/task routing and secret safety | Live negative boundary is useful after the positive setup; cheaper tests cover the full matrix | N/A | Same script, negative phase | Change one input at a time: host, absent task token, write-capable cache fixture, invalid member, and stale checkout/origin; inspect that no remote mutation or alternate-account use occurs | Parameterized unit/integration redaction and routing matrices in existing jobs | Non-zero stable failure; no fallback; no remote ref/MR change; token absent from argv, URL, config, plans, results, logs, fingerprints, bindings, diagnostics | Sanitized negative results and remote-state before/after; image only for the changed user-visible failure summary |
| C6 | MR publication with explicit member; unambiguous secret-safe effects | **Local E2E required** for one real GitLab create/update path because selected credentials cross GitLab HTTP; ambiguity and provider failures remain lower-level | GitLab REST is external and unchanged; no project OpenAPI schema | Same script, MR phase | From the explicit-member checkout, preserve a real test commit, publish/update one MR to the configured target, inspect author/assignee/description/branch facts and safe selected identity | Extend `test_gitlab.py` and output tests; no new CI service dependency | Exact project/source/target; one MR created or updated; explicit credential identity differs from assignee semantics; no secret/provider payload; idempotent repeat updates same MR | MR/commit/file links in final Multica comment, sanitized assertions, before/after output text and PNG |
| C7 | Daemon-generation credential replacement | A focused manual lifecycle check is required to prove stop → validate → restart isolation and rollback; no product rotation API or second full cache scenario is needed | N/A | Reuse the cache phase's readiness and cleanup actions; add no product rotation command | Record the first generation and children; use `multica daemon stop`; wait for exit; validate the replacement with one-shot `git credential fill`; use `multica daemon start` and `multica daemon status --output json`; perform one fetch; then repeat stop/start with the prior/no-helper environment for rollback | Lower-level tests cover immutable launch-environment projection and runtime owner/self-information mismatch; no rotation state-machine tests | Old generation and children exit before validation; after invalid validation the operator does not start; one new generation inherits only replacement inputs; rollback generation inherits only restored inputs; no persisted Git config | Commands/exits, process/generation inspection, validation result, fetch assertion, cleanup ledger; no image |

## Test Script and Fixture Contract

- Extend the existing `scripts/e2e_gitlab_member_workflow.py`; do not create a parallel runner. Add bounded phases for readiness, cache/generation lifecycle, task routing, negative routing, MR publication, and cleanup. The lifecycle phase orchestrates public stop/start plus one-shot validation; it SHALL NOT introduce or depend on a product rotation command. The script SHALL accept product revision, isolated Multica profile/root, `TEST_REPOSITORY_URL`, target branch, cache member, two task members, and owner-only credential sources. It SHALL never accept token values in argv.
- Use a fresh isolated Multica daemon/profile/cache and two fresh root tasks/checkouts. Do not use the runtime's reference checkout or another task's cache. Bind only loopback/local resources and record the exact candidate SDK SHA plus script/test revision.
- Readiness SHALL prove: exact candidate installed; stable helper executable present; repository/target accessible; configured cache login resolves to a runtime provider ID and token self-information reports that same ID, active state, and exactly `read_repository`; two task credentials are mapped to the same exact host; clean isolated roots; no conflicting daemon or Git child; capture renderer available. Failure ends the attempt before clone/fetch/MR mutation.
- Automated script assertions and the agent-driven walkthrough are separate. Tester SHALL manually invoke public checkout/Git/sync/MR surfaces and inspect Git config, remotes, safe CLI documents, refs, MR fields, and process state; a second invocation of the automated script is not the independent check.
- The script may create ordinary test commits and one MR in the test repository. Keep functional commits as produced and link them; test-only setup belongs in a separate commit only when it is a real repository fixture. Do not rename a Git-generated commit for evidence formatting.

## Images and Evidence Publication

- Because safe CLI output gains selected member/source fields, capture actual baseline and candidate text at the same terminal width, locale, output mode, and equivalent fixtures. Render genuine captured text to PNG with the bundled `scripts/render_terminal.swift`; label it as a render. Record that redirected output does not prove ANSI/TTY/progress behavior.
- Required files: `c4-auto-before.txt/png`, `c4-auto-after.txt/png`, `c4-explicit-after.txt/png`, `c5-failure-after.txt/png`, and `c6-mr-after.txt/png`. Preserve the tested output language; Tester-authored explanations remain English. Missing mandatory captures prevents complete verification.
- Publish MR, commit, and file links plus images as attachments in the final Tester comment on the Multica implementation issue. Also publish exact product/test revisions, commands, exit codes, assertions, sanitized summaries, and cleanup ledger there, then read back the comment and attachment metadata.
- Runtime logs, diagnostic JSON, raw outputs, hashes, SHA evidence, commands/exits, provenance, and screenshots/renders are Multica-only evidence and SHALL NOT be committed. Repository JSON is allowed only when it is an actual output of the tested product contract.
- Sanitize private URLs/hostnames, personal data, absolute paths, and every credential before publication. A secret-corpus scan SHALL cover all text/image source files and command/result projections.

## Execution Order, Attempts, and Cleanup

1. Verify exact candidate, focused CI results, helper packaging, isolated bindings, credential scopes, two member mappings, renderer, and cleanup ownership.
2. Run C3 cold/background cache flow, then C4 concurrent task routing, C5 negatives, C6 MR publication, and C7 generation replacement/rollback. Preserve a PASS on the same revision and unchanged fixtures; rerun only impacted cases after a candidate change.
3. Use one readiness-approved candidate run. A second run is permitted only after a diagnosed product/test correction; setup debugging before readiness does not mutate the remote and does not count as a live attempt.
4. Cleanup stops only the isolated daemon and owned Git children, removes isolated cache/profile/worktrees and temporary owner-only credential fixtures, and verifies no process or helper config remains. Preserve the evidence branch, commits, and MR. Do not close/merge the MR or delete its branch without a separate instruction.

## Estimation Evidence Contract

The estimate covers remaining implementation, focused tests/fixtures, packaging, CI integration, documentation, live-test script support, review repair, and required verification preparation for one experienced developer familiar with this Python/Click/Git stack and without AI acceleration. Unattended CI time, approval queues, external credential provisioning, and independent Tester execution are excluded. Confidence, calibration, assumptions, and evidence are recorded below the topology after the authoritative issue properties are written; numeric totals remain only in those properties.

## Estimate Interpretation

- Authoritative totals: planning issue properties only.
- Confidence: medium. Acceptance is explicit and close local analogues exist, but runtime owner/self-information compatibility, native daemon environment propagation, and two-member live fixtures add bounded uncertainty.
- Calibration: uncalibrated. Repository history shows analogous credential, encrypted-store, Git transport, MR, packaging, and E2E changes, but it contains no trustworthy active-hour measurements.
- Evidence: `gitlab_credentials.py` and its foundation tests provide the reusable resolver; `git_credential_helper.py` provides the short-lived protocol implementation; `odcli_multica.client`, `git_execution`, and CLI/tests expose the three bridge consumers; installed-artifact tests and the existing GitLab member workflow provide delivery analogues.
- Invalidation: a required credential-record/Admin-model migration, second credential store, product rotation API/state machine, Multica core protocol change, inability to validate token scope on the target GitLab, or a change from one shared cache owner to per-project cache identities requires re-estimation and a planning revision.

## Delivery Topology

`delivery_mode=dag` because the authoritative `Estimate, hours` property is above the threshold and stage 2 contains two genuinely independent work packages. Stages are consecutive topological layers; they are not status or WIP controls.

Stage mapping: `WP-01 -> 1`, `WP-02 -> 2`, `WP-03 -> 2`, `WP-04 -> 3`.

### WP-01 — Shared credential foundation

- **Task coverage:** 1.1, 1.2, 1.3.
- **Deliverable:** The existing runtime owner verification is split/reused to expose stable provider identity without stored-record changes, and the single encrypted-first, explicit-or-user-root credential precedence is implemented and regression-tested independently of cache and bridge consumers.
- **depends_on:** none.
- **Owned responsibility scope:** Existing runtime identity verification, core credential resolver, and directly related core unit fixtures/tests. Critical shared files include `src/odoo_instance_sdk/internal/gitlab_identity.py`, `src/odoo_instance_sdk/internal/gitlab_credentials.py`, and `tests/unit/test_gitlab_foundation.py`.
- **Contract surface:** Existing callers retain `GitLabCredentialContext`; the runtime verifier exposes owner identity for cache validation while mappings, encrypted records, secret-store schema, and Admin model stay unchanged; implicit project-local defaults are removed only for bridge callers after WP-03 adopts the foundation.
- **DoD / evidence:** Focused runtime owner-ID, owner-only, encrypted, user-root, explicit override, compatibility/conflict, and `ODCLI_USER_ROOT` tests pass; lint/types for touched core modules pass; no product path selects ambient credentials and no credential migration is introduced.
- **Parallel safety:** Foundation is the only writer of shared resolver contracts and completes before both consumers begin.
- **stage:** 1.

### WP-02 — Native cache helper and packaging

- **Task coverage:** 2.1, 2.2, 2.3, 2.4.
- **Deliverable:** Installed `git-credential-odcli` compares runtime owner identity with active `read_repository`-only self-information and operates through process-local Git configuration across supported packages/installs.
- **depends_on:** WP-01.
- **Owned responsibility scope:** Core helper, bounded provider self-information adapter, core helper/integration tests, project scripts metadata, packaging smoke, and numbered-install helper publication. Critical shared files include `src/odoo_instance_sdk/internal/git_credential_helper.py`, core packaging metadata, `tests/packaging/test_artifacts.py`, and directly related core tests.
- **Contract surface:** Consumes WP-01 runtime owner identity and resolver; exports the stable executable and cache-read purpose contract; does not change credential storage/Admin schemas or `odcli-multica` command signatures.
- **DoD / evidence:** Helper protocol, runtime-owner/self-information equality, exact scope/state, host rejection, secret-safe failure, cold/repeated fetch simulation, immutable generation isolation, wheel/sdist, and numbered-install cases pass with no persisted credentials or rotation subsystem.
- **Parallel safety:** After WP-01, writes remain in core helper/packaging zones; WP-03 owns `packages/odcli-multica` and does not edit these files. Shared contract changes return to planning rather than being made by either sibling.
- **stage:** 2.

### WP-03 — Verified task-member bridge and MR publication

- **Task coverage:** 3.1, 3.2, 3.3, 3.4, 4.1, 4.2.
- **Deliverable:** Raw Git, sync/push, and MR publication share typed automatic/explicit member selection, revalidation, user-root credentials, safe output, and cache-account non-fallback.
- **depends_on:** WP-01.
- **Owned responsibility scope:** `packages/odcli-multica` models, policies, client, CLI, Git execution, package tests, and directly related bridge documentation needed to explain the API. Critical shared files include `client.py`, `client_policies.py`, `git_execution.py`, `models.py`, `cli.py`, and package tests.
- **Contract surface:** Consumes WP-01 resolver and provider identity; adds optional typed `member_id`, CLI `--member-id`, and safe selection source; preserves verified context, origin/branch, assignee, issue-link, process, HTTP, and output contracts.
- **DoD / evidence:** Automatic and explicit selection, membership/type failures, execution drift, two-member concurrency, submodule hosts, all three CLI leaves, MR create/update, no fallback, and full redaction matrices pass with lint/types.
- **Parallel safety:** After WP-01, writes remain under `packages/odcli-multica`; WP-02 owns core helper/packaging. It SHALL NOT revise WP-01 contracts or core helper files.
- **stage:** 2.

### WP-04 — Consolidated CI, E2E support, and operating documentation

- **Task coverage:** 5.1, 5.2, 5.3, 5.4.
- **Deliverable:** The integrated candidate has existing-job CI coverage, one extended repeatable GitLab/Multica E2E harness, exact daemon-generation replacement/rollback, user-root/member documentation, and consolidated verification evidence.
- **depends_on:** WP-02, WP-03.
- **Owned responsibility scope:** `.github/workflows/ci.yml`, `scripts/e2e_gitlab_member_workflow.py`, package/core user documentation, cross-surface fixtures, and OpenSpec task completion after implementation. Product logic belongs to predecessors; findings that require product changes return to the owning predecessor scope and require reconsolidation.
- **Contract surface:** Consumes the installed helper and bridge CLI; uses `TEST_REPOSITORY_URL` from project context without committing private values; publishes only normal test scripts/fixtures/docs to Git and keeps runtime evidence in Multica.
- **DoD / evidence:** Existing CI jobs execute all added tests; the script exposes planned readiness/cache-generation/task/negative/MR/cleanup phases without a product rotation command; docs cover process-local config plus stop → validate → restart and rollback; scoped lint/types/tests/package and strict OpenSpec validation pass or exact CI links are provided under the local time limit.
- **Parallel safety:** Starts only after both stage-2 deliverables are consolidated, so CI/script/docs encode one stable public contract and do not race sibling write zones.
- **stage:** 3.

## Coverage and Frontier Audit

- Every OpenSpec task is assigned exactly once: WP-01 covers 1.1–1.3; WP-02 covers 2.1–2.4; WP-03 covers 3.1–4.2; WP-04 covers 5.1–5.4.
- Direct dependencies only: WP-02 and WP-03 depend on WP-01; WP-04 depends on both immediate stage-2 predecessors.
- Executable frontiers: stage 1 `{WP-01}`; stage 2 `{WP-02, WP-03}` in parallel; stage 3 `{WP-04}`. The stage-2 siblings have distinct critical write zones and a frozen predecessor contract.
- Related tests, fixtures, snapshots, docs, generated package metadata, and service files belong to the responsible WP unless explicitly owned by its active sibling; the listed paths are critical coordination points, not exhaustive allowlists.
