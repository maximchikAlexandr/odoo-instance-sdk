# Design

## Context

See `research.md` for pinned sources and `proposal.md` for scope. The selected model reverses #70's original recommendation because project resources cannot select an independent checkout per issue. Existing core removal assumes it owns the Git checkout; passing a different destination alone is unsafe.

## Goals / Non-Goals

Supply finite SDK/CLI building blocks usable from an agent, script or Temporal activity. Use one code checkout and the existing core provisioning/cleanup machinery. Do not implement a dispatcher, task starter, scheduler or universal command compositor. A native Multica task must already exist; this extension does not manufacture one for an external worker.

## Decisions

### D1. Three explicit phases, no competing checkout command

1. Call `multica_py`'s required public native repository checkout command from the active Multica task. Capture URL, explicit ref, cwd, workspace/task context and credentials privately. Multica performs its own checkout/registration. Native `multica repo checkout` remains the CLI counterpart; no `odcli-multica checkout` clone is added.
2. Call `odcli-multica env prepare PATH --issue ISSUE --run RUN --base REF (--remote NAME | --backup-id UUID | --source-db NAME)` with the exact returned path and explicit core project context. This delegates to a public core adoption command, not native core checkout. It provisions COPY only and returns the environment ID; **prepare does not bind or launch Odoo**.
3. Call `odcli-multica env bind ENV --issue ISSUE --run RUN`. It validates and records the association. Existing core runtime commands then start/wait/stop the environment when explicitly requested by the caller.

Each phase has its own immutable preview and finite execution. This is deliberately several library calls, not an atomic transaction across Git, PostgreSQL and Multica. The calling skill/script can present one scenario. No later argv is constructed inside an already executing earlier-phase command. No new CLI integrated flag is required; ordinary `odcli env checkout TICKET` and its ticket/branch allocation remain unchanged.

Do not repeat phase 1 after phase 2 has succeeded: native checkout may change a clean checkout's branch on a new task. Recover from saved environment identity using status/bind; a new code revision needs a deliberate new preparation. `--fresh`, resets and forced branch switches are never part of this flow.

### D2. Minimal deterministic project and task context

Use `MulticaOdooClient(core_client, multica_client)` with concrete frozen `ProjectLink` and `TaskContext` values. `ProjectLink` identifies the core project, Multica server origin, workspace UUID and project UUID; the repository URL comes from explicit trusted project setup and must match the selected Multica repository context. Do not choose it from free-form issue text, a project title or the first resource. Multiple repositories require an exact URL selection.

`project link`/`project show` maintain one small extension-owned `.odcli/multica.toml` beside the registered core project manifest, with atomic replacement, unchanged-file revalidation and preservation of unrelated settings. No secrets, local absolute paths or task IDs in this file. The caller already selects the core project through `--project`; no global second project registry is added. Changing a link does not rewrite existing bindings. A script may pass `ProjectLink` directly without persistence.

`context --issue ISSUE --run RUN` validates through public `multica-py` APIs: server/workspace, issue membership in project, exact run membership and execution host identity. Task environment variables are hints; conflicting explicit values fail. The actual checkout path must be a Git root beneath a proven current/durable task directory on the same execution host and match the configured repository. A relative display-only API path is not filesystem authority. Unsupported or missing host/path evidence blocks preparation/bind rather than guessing. Read all needed pages or return incomplete; never assume the first runs page is complete.

The concrete host check uses `issues.get_command`, `issues.runs_command` and `daemon.status_command`: require the selected run's runtime UUID in the local daemon's selected-workspace runtime IDs and require matching server origin. Multica v0.5.3 already publishes these facts in daemon-status JSON, but the current `multica-py` `DaemonStatus` drops them. Extend that public model/decoder additively with daemon ID, server URL, OS, status and typed workspace/runtime IDs before consuming it. Preserve `starting`/`stopped`/port-conflict/missing-field distinctions. No separate host registry or remote machine selector. Forwarded/container daemons without a proven shared filesystem are unsupported, even when localhost is reachable.

Outside an active task, an external script/worker may inspect/bind an existing same-host checkout if these checks succeed; it cannot invoke task-only native checkout with a personal token or move a checkout across machines. `context` is observational, not a role/access sandbox or proof that a dump is approved.

### D3. Generic core adoption, not a Multica-specific core API

Add `EnvironmentResource.adopt_command(project, checkout_path, *, options: EnvironmentCheckoutOptions)` and delegating `adopt(...) -> DevelopmentEnvironment`. The first release accepts COPY only, an explicit base, one explicit source selector and an existing Git working tree (linked worktree or independent clone, since native Multica supports both). It does not add a public Git/host/provider abstraction. The extension's `prepare_command` first performs context validation, then returns the captured core command directly; a retry returning an already ready matching environment uses a no-mutation core plan.

Adoption validates canonical path, repository origin identity, branch/HEAD and base commit, manifest identity, database source/provenance, target collision, disk, Python and port using current core boundaries. For the first adoption require HEAD equal to the locally resolved explicit base commit and no Git-reported tracked/untracked changes; respect Git's existing ignore rules, without a Multica-specific filename allowlist in core. Ignored credential/build files are not copied. A kept dirty checkout or cached wrong base fails with retained path and guidance, never resets. This restriction gives a reproducible initial environment; normal user edits/commits after adoption remain allowed and visible as Git context.

Use a live registered project template rather than copying `.env` or creating a second project from the checkout. Preserve its project ID even when the borrowed checkout has a different Git common directory. Store the checkout's actual Git identity separately. Rebase repo-local config/addon/dependency paths into the adopted checkout; external Odoo distribution paths remain external.

Persist additive code ownership `sdk_owned | caller_owned | unknown`, an explicit SDK artifact root, core project identity and checkout identity. Existing valid SDK-owned rows retain behavior when ownership is proved from their canonical layout; ambiguous legacy rows remain unknown and cannot authorize code deletion. Do not add a second environment catalog or treat the parent of an external checkout as an SDK root.

Allocate generated config/log/lock/optional venv under the existing global environment root, independently of the borrowed code. Writable filestore and target database are unique COPY resources under existing ownership checks. No symlink from an owned root may grant cleanup access to the borrowed root. Reuse source-neutral restore evidence, COPY journal, neutralization, backup protections and optional post-success retention from the predecessor. No default-source switch and no additional backup GC implementation. `create_venv` remains false unless explicitly selected by the caller.

Idempotency is by core project ID + canonical checkout identity, not task title/branch. Lock and reserve before effects. Same source/base/options and a ready record return the same environment without restore/download or dependency changes. Incompatible inputs conflict. An existing failed/creating/cleanup-failed record reports exact recovery state; it is not silently overwritten or treated as ready. Base/source identity remains captured history even if named configuration later changes.

Resolve a matching ready record before applying first-adoption clean/HEAD-equals-base checks. A retry on the same recorded branch may contain ordinary subsequent edits/commits and still returns its existing UUID. Never reinterpret that retry as a request to refresh the database or re-resolve a moving source/base into new inputs.

### D4. Small bindings, not remote execution routing

`env bind` operates only on a ready, validated environment and a proved run/checkout relation. Use one private local binding file per environment in the canonical configured project's ignored `.odcli/multica/bindings/<environment-uuid>.json`, containing schema version, core project/environment UUIDs, Multica origin/workspace/project/issue/run/runtime identifiers, canonical path and captured Git identity. Files/directories have owner-only permissions and are never written into the daemon's code checkout. This is extension association metadata, not a copy of the core catalog. It is keyed by environment, and each run association is unique: an issue can have multiple runs/environments; never key on issue alone. A reused environment can append another verified run for the same issue, with no duplicate; conflicting issue/project identity requires explicit unbind first.

Do **not** publish this file or credentials as an attachment, copy a developer-machine path into task descriptions, alter project resources, or rewrite Multica run workdirs. Native Multica already owns the task/checkout association. The local record adds Odoo environment identity; workflows can obtain a compact secret-free result for their own audit. Remote issue metadata is not required in this slice: it would add a second writable binding store and overwrite/concurrency problems without changing checkout routing.

`env unbind ENV` removes only this extension file under an extension-owned per-environment file lock with identity revalidation. It never stops a run, changes task status or removes Git/DB/config/filestore. Core environment removal does not depend on the extension: an association left behind becomes `stale` and explicit unbind removes it. A core removal racing with bind may leave a stale association, but cannot recreate core artifacts or authorize later mutation; bind re-reads the core record after writing and reports the race, and every later use revalidates. Do not expose the core's private lock implementation to solve this harmless metadata race. Mutation results identify whether anything changed.

### D5. Diagnostics and recovery are finite and truthful

`env status [ENV]`/`status_command` returns one typed bounded snapshot: local environment/path/branch/revision, issue/run identifiers, binding state, core provisioning state and separately observed runtime state when available. Binding states: `bound`, `unbound`, `stale`, `conflict`, `unavailable`. Include reason code, observed time and suggested existing next command. Core provisioning `ready` must not be labelled HTTP-ready.

Missing path, replaced Git identity, wrong host, conflicting association and inaccessible Multica remain distinguishable. Network failure does not erase local evidence or block unbinding/SDK-owned cleanup. Status performs no repair, download, registration or task action. Ordinary edits/commits do not themselves make a binding stale; path/repository/branch replacement and changed routing do.

After a native checkout failure/timeout, report unknown outcome and inspect before retry; do not delete a path because a response was lost. After adoption failure, preserve the native checkout and report core recovery IDs. After successful adoption but bind failure, retain the environment and retry bind only. No distributed rollback guarantee, background retry or automatic task rerun.

### D6. External checkout lifetime and cleanup

All cleanup, sync, run, diagnostics, inventory and cwd-resolution paths must honor separate project identity and code ownership. For caller-owned code, removal skips Git removal, reset, pruning, branch deletion and code-root recursion regardless of dirtiness; it still checks exact database/filestore ownership and active Odoo runtime before removing owned assets. Missing/replaced code must not prevent safe cleanup of independently proven SDK-owned assets. Unknown asset ownership fails closed.

Multica can eventually GC its managed checkout. The extension neither patches GC markers nor promises a durable lease that the public API does not provide. Workflows must stop/remove Odoo resources explicitly before releasing a task checkout when feasible; otherwise status reports stale and core cleanup by UUID remains available. Concurrent upstream deletion cannot be prevented by an SDK lock: no unconditional lifetime guarantee is claimed. This is a task analysis workspace, not a permanent hosted environment.

### D7. Packaging and public execution boundaries

`packages/odcli-multica` is a separate distribution, `odcli_multica` import package and `odcli-multica` executable; reuse the #69 workspace scaffold without moving core. No imports from core `internal.*`, `storage.*`, private command methods or Multica private modules. Core process operations stay in `internal/proc`; Multica operations use the external SDK's public captured commands and its existing transport, never a second runner in the extension. Pure extension file actions use a concrete immutable action command; do not invent a generic execution framework.

All public mutating/spawning operations have inspectable command siblings and delegating convenience methods. Core prepare returns the exact core adoption command. Other extension commands expose frozen concrete plans/results; their ordered child SDK plans and filesystem actions are captured before effects. Dependencies of a later phase are resolved by the caller between commands, not via hidden continuation callbacks.

Standalone CLI uses thin SDK delegates and bounded Rich/JSON/TOON results. Publish a narrow supported facade `odoo_instance_sdk.cli_output` over the existing `commands.output` document emission/model boundary for this concrete extension consumer; keep format selection and writing in that output adapter, not domain callbacks. Do not copy serializers or create a renderer hierarchy. Reuse a compatible facade if #69 has already published it. Register any added core leaf in existing `PUBLIC_LEAF_CASES`; use one package-local inventory for standalone extension leaves. Preserve the single core inventory. No watch or dynamic Click injection is needed.

### D8. Proposed public surface

Names below are proposed extension API, not a claim that these methods already exist. All resource methods have matching immutable `*_command()` siblings; reuse existing core parameter/result types where shown.

| Resource operation | Explicit inputs | Result / CLI |
|---|---|---|
| `client.projects.link` / `show` | core project, `ProjectLink` for writes | `ProjectLink`; `project link/show` |
| `client.context` | project link, issue ID, run ID | frozen `TaskContext` with daemon/runtime/path evidence; `context` |
| `client.environments.prepare` | project, checkout path, issue/run IDs, `EnvironmentCheckoutOptions` | existing `DevelopmentEnvironment`; `env prepare` |
| `client.environments.bind` | project, environment UUID, issue/run IDs | frozen association plus changed/no-op; `env bind` |
| `client.environments.status` | project, optional environment UUID | bounded frozen binding/provisioning/runtime facts; `env status` |
| `client.environments.unbind` | project, environment UUID | removed/no-op; `env unbind` |

Context reads are explicit observational work before capturing preparation, not mutable authentication state inside a core plan. The returned core plan revalidates captured local checkout/source identities; it does not promise a remote task cannot finish while restoring Odoo. Binding re-reads remote context after preparation. A lost task/lifetime race is a recoverable partial outcome, not a reason to reset or delete borrowed code.

## Risks / Trade-offs

- **Extra adoption primitive** → necessary to avoid claiming ownership of daemon code; reuse provisioning rather than duplicate it.
- **Multica SDK lacks checkout** → prerequisite in `multica-py` with versioned fixtures; no raw API escape hatch. Current local 0.5.2 is not proof of the new 0.5.3 combination.
- **Prerequisite work not merged** → wait for MYL-271/272 and #69 contracts; do not implement substitute APIs in the extension.
- **Run paths hidden by upstream API/permissions** → unsupported diagnostic; request the required upstream evidence, never guess an absolute path.
- **Public ABI lacks reusable output adapter** → one concrete adapter in core, not a plugin/renderer hierarchy.
- **No cross-machine provisioning or permanent checkout lease** → execute on the owning runtime; orchestration/lifetime policy belongs to the caller.

## Migration Plan

1. Verify predecessor APIs and the minimal multica-py checkout operation on the selected CLI release; do not disable compatibility checks.
2. Add one linear catalog migration for adoption ownership/project identity with conservative legacy treatment; implement adoption and all affected cleanup/runtime resolution before exposing it.
3. Add extension member, context/link commands, prepare, binding/status/unbind and isolated-wheel tests. Bind existing adopted environments explicitly; no automatic migration of Multica project resources.
4. Publish only after fake-boundary and controlled disposable-daemon acceptance prove same-checkout execution and safe cleanup. Existing normal checkout remains unchanged.

Rollback: uninstall the extension without deleting environments; clean SDK-owned resources by exact UUID with a compatible core. Catalog migrations remain forward-only; do not run an older core against upgraded ownership rows.
