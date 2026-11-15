## Delivery Contract

- Task key: `MYL-595`
- OpenSpec change: `integrate-multica-git-credentials`
- Approved base: `f0918da2d36b59c35cf0a949efc58744d1efb7f9` (`origin/main`)
- Planning revision: `4`
- Delivery mode: `single_wp_no_dag`
- Topology source: authoritative `Estimate, hours` property on the planning issue
- Estimate source of truth: `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours` properties on the planning issue; numeric totals are intentionally not duplicated here

The authoritative estimate is within the single-work-package threshold. Exactly one implementation work package covers every OpenSpec task. It has no `depends_on`, stage, frontier, or DAG orchestration.

## Scope and Reuse Boundary

Implementation SHALL connect existing paths rather than build parallel machinery:

`cache-read validation / installed alias → existing Git credential helper → existing encrypted-first resolver → existing RootCreatorContext and execution revalidation → existing credential snapshot and Git/GitLab transports`

The work excludes provider-ID persistence, credential-record/Admin migrations, a new selector/result abstraction, a second resolution subsystem, hot-rotation product machinery, and duplicate broad MR/redaction/context verification.

## E2E Plan

`e2e_required: true`

Reason: unit and integration tests cannot prove installed helper discovery by Git, process-local inheritance across a real daemon generation, real GitLab token self-information, isolated two-account routing, or absence of project-local credential copies in real task checkouts. No Odoo server, database, filestore, or numbered Odoo slot is required because the affected boundary is Multica checkout plus Git/GitLab authentication.

### Test environment and scripts

- Extend the existing `scripts/e2e_gitlab_member_workflow.py`; do not create a second runner unless a thin reusable setup helper is required.
- Use the test repository configured in the Multica project description as a private runtime input. Do not commit its private URL, hostname, credentials, or member data.
- Create a fresh isolated Multica daemon profile/cache and two fresh task checkouts for the exact implementation SHA. Tester SHALL not use a reference checkout, another task's checkout, or another daemon's cache as the test worktree/state.
- Keep account fixtures and tokens in owner-only user-root storage outside Git. Readiness SHALL validate both human members, exact hosts, the cache account's exact standard `read_repository` scope, installed helper, daemon isolation, clean evidence paths, and repository reachability before mutation.
- Planning does not execute the scenarios.

### Focused verification cases

#### C1 — Cache identity, scope, and installed helper

Tester SHALL invoke the installed helper through real Git credential configuration and prove: configured login/runtime provider ID equals token self-information owner; active exact `read_repository` succeeds; broader, granular, inactive, mismatched, incompatible, wrong-host, malformed, or missing-purpose inputs fail without fallback or secret output.

#### C2 — Explicit member and user-root boundaries

Tester SHALL prove automatic root-creator behavior remains unchanged, a valid explicit current human member wins, invalid/non-human/cross-workspace/stale selection fails before credential delivery, and bridge operations work with credentials only under the effective user root while project `.odcli` credential files are absent.

#### C3 — Two-account isolation

Tester SHALL run isolated task operations for two members against the same repository and prove each uses only its own exact-host credential snapshot. A missing selected credential SHALL fail without root-creator, cache-account, or cross-account fallback. Existing MR/redaction/context behavior is inspected only where the new member/source observation changes output.

#### C4 — One integrated live path and rollback

Tester SHALL stop the isolated daemon generation, validate the cache credential with the installed helper, start with process-local helper/member/purpose configuration, perform a cold cache fetch, then run automatic-root and explicit-member task Git operations from the isolated checkouts with no project credential copies. Tester SHALL capture the safe observable member/source transition, stop the generation, restore the prior launch environment, start again, and prove replacement inputs are absent. No hot-rotation API or parallel generation is expected.

### Independent Tester actions and assertions

Tester SHALL inspect the exact implementation SHA and changed tests before setup; independently choose unused slot resources; verify the private fixtures rather than trusting Implementer output; execute each assertion directly; inspect remote URLs, effective Git configuration, process-visible command data, Multica results, and sanitized failures for secret absence; and compare the observed member/source with the intended account. Tester SHALL not edit product tests or fixtures to obtain a pass.

### Images and evidence

- Capture actual terminal text before and after the observable member/source change on macOS and render PNGs with the bundled `scripts/render_terminal.swift` from `openspec-e2e-testing`. Preserve the tested output language and document ANSI/TTY/progress limitations honestly.
- Attach PNGs, sanitized command/output logs, assertions, diagnostic JSON, exact SHA/hashes, environment provenance, cleanup result, and MR/commit/file links to the final Multica Tester comment, then read back attachment/link presence.
- Commit only genuine test-repository results produced by the verified Git utility and required functional fixtures/scripts. Keep tests and functional results in separate commits when both exist. Do not commit logs, screenshots, evidence JSON, secrets, private URLs, or generated diagnostics.
- Cleanup SHALL remove the isolated checkouts, daemon/profile state, temporary credential fixtures, and transient cache data while preserving the evidence MR/branch/commits. It SHALL not merge, close, or delete the branch without separate authority.

## Work Package

### WP-01 — Reuse-first credential integration

- Tasks: `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `2.4`, `3.1`, `3.2`, `3.3`
- Deliverable: one coherent implementation that adds cache-read validation/entrypoint to the existing helper, minimally extends the existing bridge context for an explicit member, removes implicit project credential defaults, and supplies focused tests, E2E support, and documentation.
- Owned responsibility scope: existing core GitLab identity/helper/resolver and packaging surfaces; existing `packages/odcli-multica` context/client/model/CLI/Git/MR surfaces; directly related tests, CI configuration, E2E script, fixtures, and documentation.
- Contract surface: `git-credential-odcli` installed alias if absent; cache-read purpose/member/host inputs; optional SDK `member_id` and CLI `--member-id`; safe selected-member/source observation; existing resolver and stop/validate/start interfaces.
- Definition of done/evidence: every OpenSpec task completed once; focused unit/integration/package tests pass; strict OpenSpec-derived contracts are traceable; no provider-ID storage, selector subsystem, project-default lookup, or rotation machinery appears; the independent E2E cases are executable from the exact SHA; documentation matches observable behavior; secrets remain absent from public/persisted surfaces.
- Parallel safety: not applicable. This is the sole all-covering work package required by the authoritative estimate threshold.

## Task Coverage Audit

Every task in `tasks.md` is owned exactly once by `WP-01`; there are no uncovered or duplicated tasks.

## Estimate Interpretation

The estimate was produced with `estimate-openspec` from this revision and the current implementation. Confidence is medium: the principal paths and reusable tests are already present, while live GitLab self-information compatibility, installed packaging behavior, and two-account E2E readiness carry bounded integration uncertainty. Calibration assumes no provider/schema migration, no new selection subsystem, no rotation API, no rewrite of existing MR/redaction/context behavior, and extension of the current E2E runner. Authoritative numeric values remain only in issue properties.
