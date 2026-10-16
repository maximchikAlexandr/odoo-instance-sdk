## 1. Shared credential and repository foundations

- [x] 1.1 Add frozen public Git/GitLab identity, repository, clone, commit, sync, upload, and merge-request result models plus stable typed failures, keeping secret fields private and JSON projections redacted.
- [x] 1.2 Move/adapt the existing `odcli-multica` mapping parser, host normalization, askpass channel, and transport preflight into core global-user-root policies; enforce owner-only `credentials/gitlab-credentials.toml` and `.env`, exact member/host selection, and compatibility adapters for the extension.
- [x] 1.3 Implement bounded GitLab `/user` verification and verified author/committer identity resolution through the existing HTTP action boundary, including login mismatch, missing identity, concurrent-member isolation, and secret-echo redaction tests.
- [x] 1.4 Implement the explicit generic repository resolver and immutable revalidation snapshot for canonical root, Git common dir, origin, host/project, branch, staged state, and initialized-Odoo classification.

## 2. Generic Git command builders

- [x] 2.1 Implement public `clone_command()`/delegating clone operation through `internal/proc` for absent destinations, with exact HTTPS validation, private askpass delivery, frozen output, dry-run, and destination/origin revalidation.
- [x] 2.2 Implement retry-safe recursive submodule inspection/initialization that preserves the current branch/worktree, validates every resolved nested URL/config/helper/hook/protocol boundary before credentials, initializes only missing submodules, and covers hostile/cross-host fixtures.
- [x] 2.3 Extend public commit command construction with explicit repository/member context, verified `GIT_AUTHOR_*`/`GIT_COMMITTER_*`, generic ticket subjects, staged-only/protected-branch guards, and unchanged initialized-Odoo formatting/history behavior.
- [x] 2.4 Generalize the existing sync state machine to explicit HTTPS repositories and selected-member credentials while preserving clean-state, fetch/rebase, same-name push, exact lease, no-retry, conflict, and stale-context behavior; prove submodule sync never mutates the parent gitlink.

## 3. GitLab merge-request publication

- [x] 3.1 Promote/adapt the existing exact-project GitLab MR transport into core and implement public `merge_request_command()`/delegating operation for explicit repository, member, target, title, bounded UTF-8 description, unique open-MR create/update, dry-run, and sanitized failure contracts.
- [x] 3.2 Add bounded image validation, GitLab project upload, digest marker/link replacement, same-image no-upload idempotency, orphan-upload diagnostics, and tests proving images never enter Git.
- [x] 3.3 Retain `odcli-multica` API/CLI behavior through compatibility adapters and update its credential/Git/MR tests to use the shared core policies without adding a core Multica dependency.

## 4. Self-update and package preservation

- [x] 4.1 Extend supported uv-tool provenance, journal, and snapshot models to capture the normalized direct requirement and optional-extra set; reject ambiguous composition before mutation.
- [x] 4.2 Use the preserved extra set in target install, rollback, and recovery argv, and add new-interpreter dashboard dependency plus packaged-SPA verification before update commit/cleanup.
- [x] 4.3 Add isolated packaging regressions that build the existing UI/package artifacts, update both core-only and `[dashboard]` uv-tool installations to an exact revision, restart headless monitor, and assert its documented health/API response without manual reinstall.

## 5. CLI, documentation, and architecture contracts

- [x] 5.1 Register `odcli git clone`, explicit `--repo`/`--member-id` commit and sync options, and `odcli git mr create`; add SDK-first `PUBLIC_LEAF_CASES`, help/usage, confirmation, dry-run, and Rich/JSON/TOON contract coverage.
- [x] 5.2 Reconcile README examples and the complete command inventory with executable Click registration, document credential file schema/location and A plus superproject-submodule workflow, and state the no-GitHub/no-implicit-init/no-standalone-push/no-hidden-gitlink boundaries.
- [x] 5.3 Update architecture/security inventories and tests so every new process uses `internal/proc`, every HTTP/filesystem mutation is an action step, no new direct launcher/client seam or imprecise production annotation is introduced, and all credential projections remain redacted.

## 6. Automated and independent verification support

- [x] 6.1 Add unit matrices for permission/mapping parsing, GitLab identity, URL/host/submodule validation, generic-vs-Odoo commit policy, sync leases, MR ambiguity/image idempotency, update extras, and all negative-before-effect cases.
- [x] 6.2 Add offline integration fixtures for multi-repository clone/retry/commit/sync plans, two member mappings on one host, core/extension compatibility, GitLab upload/MR request sequencing, and update rollback/recovery requirement preservation.
- [x] 6.3 Add a parameterized `scripts/e2e_gitlab_member_workflow.py` (or repository-conventional equivalent) that uses only supplied private repository-role and selected-member inputs to exercise A, recursive B with B/ours and B/odoo, and the independent repository under one authorized GitLab user, with two retained feature branches/MRs, repeat image publication, assertions, sanitization, and failure-safe local cleanup. Retain two-member isolation as offline test coverage; live two-user verification is deferred.
- [x] 6.4 Add a parameterized `scripts/e2e_update_dashboard.py` (or repository-conventional equivalent) that creates an isolated supported uv-tool install, records the baseline composition, updates to the candidate SHA, restarts headless monitor on an allocated port, probes the documented API, and cleans local state while retaining published Git evidence.
- [x] 6.5 Run focused unit/integration suites, `packages/odcli-multica` tests, CLI/architecture/security contracts, dashboard/packaging tests, `make lint`, `make types`, and the repository offline test suite; record exact commands and exit codes without committing logs or evidence artifacts.
