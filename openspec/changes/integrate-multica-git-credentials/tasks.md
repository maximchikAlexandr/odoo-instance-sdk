## 1. Shared credential contracts

- [ ] 1.1 Split/reuse the existing runtime GitLab owner verification so cache-read validation authenticates the configured login and obtains its stable provider user ID without changing stored mappings, encrypted records, the secret-store schema, or the Admin model.
- [ ] 1.2 Refactor `resolve_gitlab_credential()` to implement encrypted-first, explicit-or-user-root legacy precedence with `ODCLI_USER_ROOT` and fail-closed compatibility-file conflicts without changing bridge callers.
- [ ] 1.3 Add focused core tests for encrypted records, user-root-only mapping/tokens, explicit path/process overrides, selected `ODCLI_USER_ROOT`, invalid permissions, conflicts, and absence of project-local copies.

## 2. Native cache helper integration

- [ ] 2.1 Add bounded GitLab token self-information validation for cache-read purpose, requiring its user ID to equal the runtime owner-verification ID plus active state and an exact standard scope set of `read_repository` before returning a credential.
- [ ] 2.2 Harden the existing helper's standard-protocol parsing and purpose/host/member validation, keep all failures secret-safe on stderr, and install the stable `git-credential-odcli` entrypoint without duplicating the resolver.
- [ ] 2.3 Add unit and integration tests for runtime owner/self-information agreement, cold-cache and repeated/background fetch routing, exact-host rejection, malformed/unsupported helper requests, read-only versus broader scopes, provider incompatibility, cache/task non-fallback, and concurrent isolation.
- [ ] 2.4 Extend packaging and installed-artifact smoke coverage to prove the helper exists in wheel, sdist, and numbered installs and works through process-local Git configuration with no credential material in remote URLs or Git config.

## 3. Explicit Multica member selection

- [ ] 3.1 Add a frozen typed selection result and one shared selector that preserves automatic root-creator traversal, validates an explicit UUID as one current human in the verified workspace, records `root_creator` or `explicit`, and revalidates before effects.
- [ ] 3.2 Thread optional `member_id` through credential-dependent raw Git, sync/push, and merge-request command/convenience methods while preserving verified checkout/repository/host, stale-plan, branch/origin, subprocess, and HTTP boundaries.
- [ ] 3.3 Add `--member-id` to the three credential-dependent `odcli-multica` CLI leaves, keep callbacks as typed pass-throughs, and include only the selected safe identity/source in dry-run and success documents.
- [ ] 3.4 Add parameterized bridge tests for automatic and explicit selection, invalid/non-human/cross-workspace members, two checkouts selecting different members concurrently, execution-time drift, submodule host routing, cache-account non-fallback, and CLI/SDK parity.
- [ ] 3.5 Adopt the WP-01 credential resolver/default precedence in every credential-dependent bridge caller, remove implicit project `.odcli` mapping/dotenv defaults, preserve explicit legacy paths, and add focused bridge regressions for user-root-only credentials and absent project-local copies.

## 4. Merge-request and secret-safety regressions

- [ ] 4.1 Update merge-request publication to use the shared selected credential without altering issue-link, exact-project, unique-MR, assignee-precedence, or provider-consistency contracts.
- [ ] 4.2 Extend Git transport, output, redaction, and MR tests to prove tokens and authorization data never enter argv, URLs, Git config, plans, results, logs, fingerprints, binding files, or diagnostics across both selection sources.

## 5. CI, live verification support, and documentation

- [ ] 5.1 Extend the existing offline and package CI coverage in `.github/workflows/ci.yml` only as needed so all new core, bridge, packaging, and platform-specific helper tests run in their existing jobs without a new test framework.
- [ ] 5.2 Extend `scripts/e2e_gitlab_member_workflow.py` with isolated cache-read, daemon-generation replacement/rollback, automatic-root-creator, explicit-member, two-member concurrency, negative-routing, and cleanup phases for the project test repository; keep credentials owner-only and emit only bounded sanitized summaries.
- [ ] 5.3 Document exact daemon process-local Git configuration, cache credential prerequisites, stop → validate → restart and rollback, user-root precedence, `--member-id` behavior, selected-identity output, GitLab compatibility, and the limitation that cached objects do not prove member authorization.
- [ ] 5.4 Run the scoped lint, type, offline bridge/core, packaging, and strict OpenSpec gates on the consolidated revision, and preserve the exact commands and exit codes for review without committing runtime evidence.
