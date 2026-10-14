## Context

Core `odcli git` resolves `GitResource` through an initialized project and currently publishes only through an SSH origin. `packages/odcli-multica` already contains owner-only TOML parsing, project `.env` loading, exact-host normalization, a private askpass channel, transport preflight, and exact-project GitLab merge-request logic, but it derives credentials from a root issue creator and is unavailable before project initialization. The requested workflow instead names a Multica member UUID explicitly and must work against any allowed GitLab root, including a superproject submodule.

The process architecture requires immutable, inspectable plans and all child launches through `internal/proc`; HTTP and bounded filesystem effects use action steps. Public output and fingerprints must remain secret-free. The existing self-update coordinator builds `uv tool install --force` from only the Git revision, so it loses the installed `[dashboard]` extra.

## Goals / Non-Goals

**Goals:**

- Resolve one exact `(member UUID, HTTPS host)` credential before any Git/GitLab request and verify its GitLab owner.
- Operate on explicit repository roots without `odcli init`, while preserving existing project-aware Odoo behavior.
- Make clone/submodule retry, commit identity, sync, MR description/image publication, and failure semantics frozen, typed, and secret-safe.
- Preserve the installed uv-tool extra set across update and prove monitor packaging after replacement.
- Keep one implementation boundary for Git processes and one bounded GitLab HTTP transport.

**Non-Goals:**

- Discovering a member from the runtime owner, issue creator, assignee, or ambient Git configuration.
- Adding GitHub support, a standalone `push` command, implicit project initialization, or hidden superproject gitlink commits.
- Managing arbitrary Git credential helpers, SSH keys, or non-GitLab providers.
- Committing uploaded MR images or automating merge/branch deletion.

## Decisions

### 1. Put pre-clone credentials under the global OdCLI user root

Use `get_user_root()/credentials/gitlab-credentials.toml` for non-secret mappings (`user_id`, normalized `host`, `login`, `token_key`) and `get_user_root()/credentials/.env` for token values. Require owner-only directory/file permissions and atomic reads. Process environment overrides `.env` for the named token key, matching existing project environment precedence, but neither ambient Git credentials nor unrelated environment variables are consulted.

Alternative: keep `.odcli/gitlab-credentials.toml` in each project. Rejected because clone must authenticate before a project or destination exists and duplicating mappings increases drift.

### 2. Promote the reusable credential and GitLab policies into core

Move or adapt the narrow parser, exact-host normalization, private credential handle, askpass channel, transport preflight, and GitLab request logic from `odcli-multica` into core internal modules. Keep compatibility adapters in the extension instead of maintaining two security implementations. Verify `GET /api/v4/user` against the configured login and capture returned stable user identity before constructing any mutating plan.

Alternative: make core depend on `odcli-multica` or `multica-py`. Rejected because the core wheel must remain independent and the new contract needs only an explicit UUID plus GitLab verification, not a Multica API client.

### 3. Resolve explicit repository context independently of Odoo context

`odcli git clone` takes URL and destination; other new paths take `--repo`. A generic repository resolver canonicalizes the root, proves it is a Git worktree, reads the exact origin, and binds it to the credential host. Existing project-aware `GitResource` behavior is reused when the explicit root is the resolved initialized Odoo project/worktree. Otherwise commit validation is repository-neutral: staged files only, protected-branch guard, verified author/committer environment, and a subject of `<ticket>: <description>` when `--ticket` is supplied.

Alternative: register every repository with `odcli init`. Rejected because it would invent Odoo state for non-Odoo repositories and contradict the pre-clone/generic-root use case.

### 4. Model clone/submodule work as one frozen command lifecycle

For an absent destination, clone captures the destination, normalized remote, optional recursive intent, all required host mappings, and Git process steps. For an existing matching repository, retry never reclones or switches/resets the current branch; `--recurse-submodules` inspects and initializes only unpopulated submodules recursively. Before a credential is issued, `.gitmodules`, resolved submodule URLs, protocol/file allowances, helper overrides, hooks, and repository-owned command overrides are validated. Each host needs a mapping for the selected member.

Alternative: call `git clone --recurse-submodules` again or reset to the remote default. Rejected because retries would destroy local branch intent or fail on an existing destination.

### 5. Extend sync rather than add push

Generalize the existing fetch/rebase/same-branch publication state machine to an explicit repository root and HTTPS askpass. It retains clean-tree, protected/detached branch, upstream, remote-SHA, exact lease, and no-retry checks. Generic repositories skip Odoo commit-format validation; initialized Odoo contexts retain it. Submodule sync never stages or commits the parent gitlink.

Alternative: add `odcli git push`. Rejected because it duplicates safe publication policy and weakens the inspect/rebase/lease sequence.

### 6. Make MR creation an idempotent repository-scoped action

`odcli git mr create` derives the GitLab project only from the verified `--repo` origin. It reads a bounded UTF-8 description file, locates the unique open MR for source/target, and creates or updates it. When `--image` is supplied, the client hashes the regular bounded local file and maintains an invisible digest marker plus the GitLab upload Markdown in the description. A matching marker reuses the existing link; a changed image uploads once and replaces the owned marker/link. Upload and MR mutation are separate action steps; a failed MR mutation reports that the upload may be orphaned but never retries create automatically.

Alternative: commit the image or upload on every invocation. Rejected because the request requires an out-of-repository attachment and repeatable MR updates without duplicates.

### 7. Preserve uv-tool extras from installed provenance

Extend installed provenance/snapshot state with the normalized direct requirement and requested extra names from uv tool metadata. Build install and recovery requirements from the same validated source and exact extra set; `[dashboard]` therefore survives revision replacement. Maintenance verifies the declared dashboard imports and packaged SPA assets in the new interpreter before commit/cleanup. Packaging tests build the UI through the existing `make package` path and exercise an isolated uv-tool update plus restarted headless monitor/API request.

Alternative: always force `[dashboard]`. Rejected because core-only installations must stay minimal; installation composition, not a new default, is preserved.

### 8. Keep output and docs inside existing inventories

New bounded leaves use the existing envelope/renderers and are added to `PUBLIC_LEAF_CASES`; process-spawning leaves expose public `*_command()` siblings. README inventory is reconciled against Click registration with a contract test rather than a second handwritten source of truth.

Alternative: add a parallel Git output renderer or command registry. Rejected because both already exist and are enforced by repository architecture tests.

## Ponytail full subsystem gate

| Candidate subsystem | Necessity | Existing alternative | Decision |
|---|---|---|---|
| Global member/host credential resolver | Clone needs identity before any project exists | Project-scoped resolver in `odcli-multica` | Keep a core resolver; reuse parser and private-handle patterns |
| Generic repository resolver/resource methods | `--repo` must work without Odoo registration | Project-only `GitResource` | Keep the narrow resolver and extend existing Git resource/state machines |
| GitLab MR upload action | Core CLI needs project upload and MR mutation | Extension MR transport lacks images and explicit member selection | Keep one core transport action and adapt extension callers |
| New subprocess runner | Not necessary | `internal/proc` | Remove from design; use existing boundary |
| New HTTP client framework | Not necessary | Existing bounded transport/action convention | Remove from design |
| Standalone push command | Not necessary | Existing sync publication phase | Remove from design |
| New installation profile registry | Not necessary | uv-tool provenance/snapshot metadata | Remove from design |

## Risks / Trade-offs

- [GitLab `/user` may omit a public email] → require a verified commit email returned by the authenticated API or an API-confirmed GitLab noreply identity; fail before `git commit` rather than use machine Git config.
- [Recursive submodules can cross hosts or contain hostile configuration] → enumerate and validate every resolved URL/config/hook boundary before credentials or mutation; require an exact mapping per host.
- [Moving policies could regress `odcli-multica`] → retain extension compatibility adapters and run its existing credential, Git, and MR suites against core types.
- [Image upload and MR mutation are not transactional] → use digest idempotency, no automatic non-idempotent retry, and a sanitized orphan-upload diagnostic.
- [uv metadata shapes vary] → accept only proven uv-tool provenance; unsupported or ambiguous extra metadata fails preflight with recovery/manual argv rather than silently dropping extras.
- [Live GitLab verification requires private infrastructure] → unit/CI contract tests use the existing fake HTTP boundary; independent local E2E uses the project test repository and two disposable member credentials.

## Migration Plan

1. Add core models/parsers/resolvers and compatibility imports without changing existing CLI behavior.
2. Add generic repository command builders and CLI leaves, then adapt extension Git/MR code to the shared core policies.
3. Add MR image idempotency and update provenance extra preservation with isolated packaging regressions.
4. Reconcile README/help inventories and run strict OpenSpec, architecture, security, offline, extension, dashboard, and packaging checks.

Rollback is code-only: revert the feature commit before users write the new global credential files. Existing project credential files and extension commands remain readable during migration; secrets are never migrated automatically. An interrupted self-update continues to use the existing journal/snapshot rollback contract, now with the preserved requirement including extras.

## Open Questions

None. The contract fails closed when authenticated GitLab identity lacks usable commit identity fields or when any submodule host lacks an exact selected-member mapping.
