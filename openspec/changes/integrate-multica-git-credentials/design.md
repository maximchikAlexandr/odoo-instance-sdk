## Context

`resolve_gitlab_credential()` already performs encrypted-first member/host lookup, supports the effective user root and `ODCLI_USER_ROOT`, and applies legacy conflict checks. `git_credential_helper.py` already implements the finite standard helper and calls that resolver. The bridge already verifies checkout/task/repository context, follows the root creator, revalidates at execution, and supplies one credential snapshot to raw Git, sync, and merge-request paths.

The missing boundaries are smaller: native cache fetches need a deliberately configured read-only identity before a task checkout exists; bridge callers need an optional explicit current human member; and `_resolve_credentials()` must stop supplying implicit project `.odcli` paths. The approved base is `f0918da2d36b59c35cf0a949efc58744d1efb7f9` (`origin/main`).

## Goals / Non-Goals

**Goals:**

- Authenticate native cold-cache and background fetches with one provider-verified, read-only member credential.
- Add an optional explicit member while preserving the existing root-creator and execution-revalidation contour.
- Make user-root-only credentials sufficient for bridge operations without changing the core resolver's existing storage model.
- Prove two-account isolation and secret-safe failure behavior with focused tests and one integrated live path.

**Non-Goals:**

- A new selector/result abstraction, credential store, provider-ID field, Admin model, OAuth flow, helper daemon, or mutable active account.
- A hot-rotation API, refusal subsystem, lock, or credential-generation state machine.
- Re-specifying or re-testing unchanged MR, redaction, checkout binding, context verification, or Git execution contracts.
- Changing issue ownership, MR assignee semantics, or core `odcli git mr create`.

## Decisions

### 1. Extend the existing helper only for cache-read trust checks

Use the current helper and resolver. Add a stable `git-credential-odcli` entrypoint only if the installed package lacks one. Cache mode requires explicit purpose, member, and exact HTTPS host. It reuses the owner portion of `verify_gitlab_identity()` to authenticate the configured login and obtain the runtime provider ID, then performs one bounded `GET /personal_access_tokens/self`. The helper returns credentials only when that response is active, belongs to the same runtime provider ID, and its complete standard scope set is exactly `read_repository`.

No provider ID is persisted. Ordinary task credentials keep the current identity/commit checks. Unsupported helper actions, malformed input, broader scopes, granular or unprovable scopes, owner mismatch, and endpoint incompatibility fail closed without another lookup chain.

### 2. Keep cache configuration process-local to one daemon generation

The Multica daemon launch environment clears inherited credential helpers, selects the OdCLI helper, and supplies the cache member and purpose through process-local Git configuration/environment. A daemon generation is immutable. Replacement uses existing interfaces only: stop the daemon and its Git children, validate the candidate with a one-shot helper invocation, start with the replacement environment, and verify daemon status. Rollback repeats that lifecycle with the prior environment. No product rotation mechanism is introduced.

### 3. Extend `RootCreatorContext`; do not add a selector subsystem

Credential-dependent command builders accept `member_id: str | None`. When omitted, current root-creator traversal is unchanged. When present, the current workspace-member API validates that the normalized ID is a current human in the already verified workspace, and the existing context carries that member plus safe source metadata. The existing command plan and execution revalidation compare the selected member before credential delivery or mutation.

Raw Git, sync, and MR publication reuse their current credential snapshot and transports. CLI callbacks only pass `--member-id` into those builders. An explicit member changes whose exact-host credential is used; it never changes checkout, task, workspace, repository, branch, origin, or host authority.

### 4. Remove only the bridge's implicit project credential defaults

`MulticaOdooClient._resolve_credentials()` stops synthesizing `.odcli/gitlab-credentials.toml` and `.odcli/.env` from `project_root`. It calls the existing resolver with the selected member/host, supplied process environment, effective user root, and only caller-provided explicit legacy paths. Existing encrypted-first behavior, `ODCLI_USER_ROOT`, explicit overrides, compatibility lookup, and conflict rules remain in the core resolver.

### 5. Verify only the new observable boundaries

Focused core tests cover cache owner/self-information agreement, exact scope, host/purpose parsing, installed entrypoint, and secret-free failures. Focused bridge tests cover explicit member success/failure and revalidation, unchanged automatic selection, absence of project copies, `ODCLI_USER_ROOT`, and two-account isolation. Existing MR/redaction/context suites change only where the new optional input or safe source field changes an observable contract. The existing E2E script gains one integrated live path rather than a duplicate matrix.

## Ponytail Full Gate

- **New subsystem:** Not necessary. Existing resolver, helper, `RootCreatorContext`, execution revalidation, snapshot, transports, and E2E runner are reused.
- **Stable helper entrypoint:** Necessary only as an install-stable alias for native Git; keep it thin and omit duplicate protocol or resolution code.
- **Cache self-information check:** Necessary because `fetch` cannot make a write-capable token read-only. Reuse runtime owner verification and add one bounded comparison; do not persist provider identity.
- **Explicit member support:** Necessary for the requested override. Extend the current context/revalidation types; do not add a selector/result layer.
- **Bridge precedence change:** Necessary only at `_resolve_credentials()` because the core resolver already has the required encrypted-first/user-root behavior. Remove synthesized project defaults; do not refactor storage.
- **Rotation machinery and repeated regression matrix:** Not necessary. Use stop → validate → start and touch existing broad tests only for changed observations.

## Risks / Trade-offs

- **Older GitLab lacks usable self-information** → Cache mode fails closed with a sanitized compatibility error; ordinary task credentials remain unaffected.
- **Read-only account can see too many repositories** → Use a dedicated account with minimum memberships and only the standard `read_repository` scope.
- **Selected member changes between plan and execution** → Reuse the existing execution-time context checks and compare current human membership before secret delivery.
- **Daemon environment affects unrelated children** → Scope configuration to the selected daemon profile and clear inherited helpers; task operations continue to use command-local credentials.
- **Live test needs two accounts** → Readiness checks owner-only fixtures before Git, evidence is sanitized, and cleanup preserves only non-secret commits/links.

## Migration Plan

1. Ship the thin cache-purpose/entrypoint delta and minimal bridge context/default changes with focused tests.
2. Install the candidate in an isolated numbered profile and validate the cache credential before daemon start.
3. Start one generation with process-local helper configuration and exercise the integrated cache/task path.
4. Roll back by stopping that generation and starting with the prior environment; credential records remain unchanged.

## Open Questions

None.
