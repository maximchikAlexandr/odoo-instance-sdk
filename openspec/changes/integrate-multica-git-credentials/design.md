## Context

The core package already contains `resolve_gitlab_credential()` and a short-lived standard Git credential helper. `odcli-multica` already verifies a task checkout, resolves the human root creator, and gives Git/GitLab operations command-scoped credentials through askpass or bounded HTTP. The remaining gaps are native shared-cache authentication, an explicit member override on the bridge surface, and the bridge's implicit dependence on project-local legacy files.

Native Multica cache fetches can start before a task worktree exists, so task-path inference cannot authorize them. Conversely, task-checkout operations have verified issue/workspace/repository context and must never fall back to the cache identity. The design keeps those two trust paths separate while reusing one credential resolver and one secret-redaction boundary.

The approved base is `f0918da2d36b59c35cf0a949efc58744d1efb7f9` (`origin/main`). The existing `verify_gitlab_identity()` path already authenticates the configured login and obtains the stable provider user ID at runtime. Cache mode reuses a split owner-verification portion of that path, then compares its runtime provider ID with GitLab token self-information while requiring the exact `read_repository` scope before returning credentials.

## Goals / Non-Goals

**Goals:**

- Authenticate native Multica cold-cache and background clone/fetch with one explicitly configured, provider-verified read-only credential.
- Preserve per-checkout member isolation for raw Git, sync/push, and merge-request publication, with an additive explicit member override.
- Remove implicit project-local credential-file requirements and define one user-root precedence contract.
- Keep selected identity and provenance inspectable without exposing tokens.
- Preserve current immutable command snapshots, execution revalidation, host checks, and redaction.

**Non-Goals:**

- Authorizing access to code already present in a shared cache; the helper controls remote Git authentication, not filesystem read policy.
- Adding SSH authentication, a second token store, OAuth, GCM/glab ownership, a credential daemon, or a mutable global active account.
- Changing Multica issue ownership, checkout binding, MR assignee semantics, or core `odcli git mr create`.
- Diagnosing the previously reported but unreproduced publication failure.

## Decisions

### 1. Use one stable short-lived helper in explicit cache-read mode

Package `git-credential-odcli` as the stable executable for the existing helper module. The Multica daemon receives process-local Git configuration that first clears inherited helpers and then selects `odcli`; it also receives `ODCLI_GITLAB_MEMBER_ID` and `ODCLI_GITLAB_CREDENTIAL_PURPOSE=cache-read`. This environment reaches cold-cache creation and later background fetches without repository-local or global Git configuration. The helper accepts only the standard `get` request, normalizes the requested HTTPS host, resolves that exact member/host through the existing resolver, reuses the existing runtime owner check to authenticate the configured login and obtain its provider user ID, then validates GitLab `GET /personal_access_tokens/self`. It returns credentials only when the token is active, the self-information user ID equals that runtime owner ID, and the complete standard scope set is exactly `read_repository` with no broader or write-capable scope. The stored mapping, encrypted record, secret store schema, and Admin model remain unchanged.

The cache account is configured once per daemon generation, not inferred from project lead. Shared cache keys may serve several projects, so project-lead selection would be ambiguous and could change while background work is active. The daemon environment is immutable for that generation. Credential replacement therefore uses the existing `multica daemon stop`, one-shot `git credential fill` through `git-credential-odcli`, `multica daemon start`, and `multica daemon status --output json` interfaces. The operator waits for the stopped daemon's Git children, validates before start, and verifies the new daemon identity/status. Rollback repeats the same lifecycle with the prior environment. There is no product rotation API, mutable rotation state, refusal subsystem, or new lock service.

Alternative considered: a project/group deploy token. It offers narrow repository reach, but it does not fit the existing member/host resolver or its owner verification and would require a second credential schema and resolution path. A dedicated personal or service account token limited to the provider's `read_repository` scope reuses the existing store and is mechanically verifiable.

Alternative considered: repository/global Git configuration. It would persist helper selection in shared state, affect unrelated commands, and create rotation races. Process-local Git configuration inherited by one daemon has the required cold-cache coverage without those side effects.

### 2. Keep cache and task identities fail-closed and non-fallback

Cache mode is accepted only when the explicit purpose and member ID are present. Task operations never consult cache configuration. `odcli-multica` continues to begin with `VerifiedTaskContext`; it chooses either an explicit member or the root creator, resolves the exact remote host, and sends one private snapshot through the existing command-scoped transport. Unknown host, missing member, invalid context, or unavailable credential ends the operation; no path falls back to another account.

Submodules reuse the same selected task member for every prevalidated host. Cache fetches use only the cache member. The helper never derives identity from branch names, instruction files, directory naming, ambient Git credentials, or the first available token.

### 3. Add one shared member-selection step to bridge operations

Credential-dependent `MulticaOdooClient` command builders accept additive `member_id: str | None`. A shared selection function normalizes the UUID, verifies that an explicit ID identifies one human member of the already verified workspace, or otherwise reuses root-creator traversal. It returns the member ID plus source `explicit` or `root_creator`. Raw Git passthrough, sync/push, and MR publication consume that result; CLI callbacks only pass `--member-id` through.

Planning captures the non-secret selected member and source. Execution revalidates workspace membership, task/repository binding, host, branch/origin facts, and the selected identity before credential delivery or mutation. Explicit selection changes only whose credential is used; it does not authorize a different checkout, task, workspace, repository, project, or host.

Alternative considered: read `ODCLI_GITLAB_MEMBER_ID` directly in CLI callbacks. That would bypass the typed SDK surface and make preview/revalidation inconsistent. The environment variable remains a private helper integration input, not the bridge API.

### 4. Make the core resolver's existing user root authoritative by default

`odcli-multica` stops synthesizing project `.odcli` mapping and dotenv paths. The resolution order is:

1. exact encrypted member/host record under the effective `ODCLI_USER_ROOT`;
2. legacy mapping from an explicit `mapping_path`, otherwise `<user-root>/credentials/gitlab-credentials.toml`;
3. token value in the supplied process environment;
4. when supplied, the explicit `environment_path` as the complete file override;
5. `<user-root>/.env`;
6. compatibility-only `<user-root>/credentials/.env`, with the existing conflict/deprecation behavior.

An explicit legacy path does not override a valid encrypted record. Project-local files work only when callers explicitly pass their paths; absence of `.odcli` credentials is normal. `ODCLI_USER_ROOT` and an explicit typed `user_root` flow through the same resolver rather than being interpreted by the bridge.

### 5. Reuse the existing execution and redaction boundaries

The helper remains a finite process and writes protocol fields only to stdout; sanitized failures go to stderr and never include secrets. Bridge Git operations keep askpass/channel delivery, frozen command plans, origin/branch revalidation, and `internal/proc`. GitLab publication keeps bounded HTTP and non-idempotent request rules. No token, authorization header, helper payload, remote userinfo, or secret-derived fingerprint is added to plans, argv, config, binding files, results, or diagnostics.

## Ponytail Full Gate

- **Stable helper executable:** Necessary because native Git needs an install-stable command; `python -m ...` is environment-specific. Keep the executable as a thin alias over the existing helper, with no second protocol implementation.
- **Cache read-scope validation:** Necessary because invoking `fetch` does not make a write-capable token read-only. Reuse/split the existing runtime owner check and compare its provider ID with one bounded self-information response; do not add a provider-ID field, record migration, Admin-model field, cache credential registry, or daemon.
- **Shared member selection:** Necessary for `--member-id`; the existing root-creator and workspace APIs are the alternative and are reused inside one selector. Keep the selector; remove callback-local or helper-local identity logic.
- **Credential replacement machinery:** Not necessary because process-local environment is immutable for one daemon generation. Keep stop → validate → restart plus rollback as an operating contract; do not add hot-rotation detection, a refusal API/state machine, mutable daemon credential state, or a lock service.
- **New storage/service layers:** Not necessary. Do not add or migrate a token/provider-identity store, OAuth service, credential broker, global account state, cache lock service, or repository binding registry.

## Risks / Trade-offs

- **[Token self-information is unavailable on an older GitLab]** → Cache mode fails closed and reports a sanitized compatibility error; implementation documents the required endpoint rather than trusting an unverifiable scope.
- **[A read-only account can read more repositories than intended]** → Use a dedicated account with only required memberships and a token whose only scope is `read_repository`; exact host routing remains mandatory.
- **[Daemon configuration is inherited by unrelated Git children]** → Clear inherited credential helpers in process-local Git config and constrain the daemon profile to the selected cache account; task operations override credentials command-locally and never use the cache member.
- **[Member or task facts change between preview and execution]** → Revalidate selected membership and all existing context/origin/branch facts immediately before credential delivery or mutation.
- **[Live tests require two real member credentials]** → Keep secrets in owner-only user-root fixtures, sanitize all evidence, and fail readiness before Git when either account or scope contract is unavailable.

## Migration Plan

1. Ship the stable helper, cache-read validation, user-root defaulting, typed member selection, CLI flags, and regression tests without enabling cache authentication globally.
2. Install the exact candidate in an isolated Multica profile and validate the dedicated cache token's configured login, runtime provider ID, self-information owner/state, and scope before configuring the daemon.
3. Stop that daemon and wait for its Git children, set the process-local Git helper/member/purpose environment, start one new generation, and verify cold-cache plus background fetch. Do not add or invoke an in-process credential replacement path.
4. Verify automatic root-creator and explicit-member task operations against isolated checkouts, then publish documentation for the same configuration.
5. Roll back by stopping the new generation, restoring/removing its cache-helper environment, starting the prior generation, and retaining the unchanged credential records.

## Open Questions

None. Cache authentication uses one explicitly configured `read_repository`-only member credential; task authentication uses verified root creator unless `--member-id` selects another verified human workspace member.
