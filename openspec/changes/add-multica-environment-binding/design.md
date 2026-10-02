## Context

Multica's project/daemon-wide `local_directory` cannot select one checkout per issue. Native checkout already owns the task-to-code association, while Odoo Instance SDK currently assumes that it created and may remove each environment worktree. The integration therefore needs one generic core adoption primitive and a thin optional package, not another checkout or binding registry.

The input package used public raw `cli.command_command()` calls and local decoders because typed checkout and complete daemon status were absent. The completed `multica-py` #93 implementation now exposes typed repository checkout and complete daemon status, and integrated MYL-272 exposes named-source and exact retained-backup COPY selection. This revision records those final signatures and removes the former dependency uncertainty.

## Goals / Non-Goals

**Goals:**

- Prepare one isolated Odoo COPY environment against one native Multica checkout.
- Use only public typed dependency contracts and the repository's existing command/execution/output boundaries.
- Preserve explicit ownership, deterministic retry, diagnostics, and owned-only cleanup.
- Make planning readiness and implementation readiness independently auditable.

**Non-Goals:**

- Persistent binding or checkout registries, project-resource mutation, task routing, workflow scheduling, telemetry allocation, remote policy enforcement, or a second runtime manager.
- Reimplementation of Multica checkout/status behavior or preservation of raw CLI escape hatches for these operations.
- Raw-command compatibility fallbacks or locally re-decoded checkout/status results.

## Decisions

### D1. Native checkout and daemon identity use final typed `multica-py` operations

Use `MulticaClient.repositories.checkout_command(url, *, ref=None, fresh=False, options=None)` / `checkout()` returning frozen `RepositoryCheckoutResult(path: str)`. The integration always leaves `fresh=False`. Use `MulticaClient.daemon.status_command(*, options=None)` / `status()` returning frozen `DaemonStatus`, including `status`, daemon/device/server/CLI identity, task counters, agents and workspace projections. Per-operation profile, workspace, timeout, cwd and environment are supplied by `OperationOptions`; command execution raises the public `CommandTimeoutError`, `CommandCancelledError`, compatibility, or execution errors rather than an extension-specific decoded variant.

The verified dependency is `multica-py` package version `0.1.0` at exact merge revision `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed`; the checkout step declares native CLI minimum `0.5.3`. Because that revision has no unique release tag, extension metadata and lock evidence SHALL pin the exact revision until an equivalent uniquely versioned distribution exists; it SHALL NOT accept an arbitrary unrelated `0.1.0` artifact.

The extension SHALL NOT call `cli.command_command()` for checkout or daemon status, parse checkout stdout, decode daemon JSON, access private HTTP/storage, or copy a subprocess runner. Checkout remains a separate phase from Odoo preparation. Timeout/cancellation retains the dependency's typed unknown-outcome semantics; the integration never assumes that no checkout was created. Forced fresh checkout is excluded.

Alternative rejected: keep the two local decoders as compatibility fallbacks. That creates two contracts for the same public operations and would preserve precisely the parity gap #93 is required to close.

### D2. Explicit read-only context, no configuration CRUD

Provide a small integration client with read-only `context` and preparation operations. Inputs identify the selected core project/repository, exact checkout path, expected Multica project, issue, and run. Server/workspace/credentials come from the scoped Multica client. Repository identity comes from the explicitly selected core project; ambiguity fails.

Context fetches the expected project and issue, requires `Issue.project_id` to equal the selected `Project.id`, and enumerates the public typed `Project.resources` collection. A complete page containing exactly one matching `github_repo` resource with a typed `GithubRepoResourceRef.url` proves authoritative repository membership against the selected core Git origin. Missing, incomplete, ambiguous, or mismatched project-resource evidence fails before mutation.

Context separately uses typed issue/run and daemon-status operations to verify workspace/issue/run membership, the owning runtime/daemon, and containment of the actual Git root beneath an absolute current/durable task directory. `TaskRun.project_id` and `TaskRun.project_resources` are duplicated snapshots: when populated they SHALL be consistent, but their absence SHALL NOT override complete authoritative Issue/Project evidence. Missing run/workspace/runtime/path fields, incomplete run pagination, conflicting populated identities, relative-only paths, or forwarded/container endpoints without proven shared-filesystem evidence fail before mutation. The frozen result records verified identifiers, checkout facts, and observation time, but is not a lease.

Alternative rejected: require the platform to populate duplicated TaskRun project snapshots before task 4.2. The public Issue and Project-resource surfaces already expose the authoritative identity, so that prerequisite would delay acceptance without strengthening it. Inferring identity from task prose, branch names, path equality, or project `local_directory` remains forbidden because none proves issue-scoped ownership on the local host.

### D3. Preparation delegates to generic core adoption

Add core caller-owned-checkout adoption as an inspectable command plus delegating convenience operation. It accepts COPY only, one explicit supported COPY source, an explicit compatible base, and an existing canonical Git checkout. The observed predecessor selector is `EnvironmentCheckoutOptions(remote_name=...)` for a configured named source or `EnvironmentCheckoutOptions(backup_id=...)` for an available retained catalogue UUID; these selectors are mutually exclusive with each other and with `source_database`. Adoption reuses the existing COPY provenance, restore, readiness, retention and cleanup behavior without duplicating it. It supports linked worktrees and independent clones without creating, moving, renaming, resetting, or deleting code.

First adoption verifies repository identity, path identity, branch/HEAD, clean status, manifest, source provenance, and locally resolved base before mutation. A ready matching record is resolved before first-adoption-only clean/base checks so ordinary later development returns the same UUID without another restore. Conflicting inputs and incomplete state return typed conflict/recovery evidence.

The integration performs bounded context preflight, captures the exact core adoption command, and returns the existing environment result. Context and environment UUID remain separate caller-owned workflow results. No composite continuation framework is introduced.

### D4. Code ownership is independent from SDK artifact ownership

Persist only the additive evidence required to distinguish configured core project identity, actual checkout identity, code ownership (`sdk_owned`, `caller_owned`, `unknown`), and explicit SDK artifact root. Generated config/log/lock/optional venv, COPY database, and isolated filestore remain under existing SDK ownership rules. Repository-local paths are rebased into the adopted checkout; external configured paths stay external; source `.env` is never copied.

List/cwd/config/sync/runtime/diagnostic/remove paths use recorded project and checkout evidence rather than guessing from Git common dir or `worktree_path.parent`. Rollback/removal never resets, prunes, renames, or recursively deletes caller-owned code, even when dirty, absent, or replaced. Legacy unknown ownership never grants deletion rights.

### D5. Packaging and output stay narrow

Add `packages/odcli-multica` as an independently versioned distribution/import/executable using the shared workspace scaffold and the verified exact dependency revisions until equivalent uniquely versioned releases exist. Core does not import Multica. The extension uses existing public bounded output contracts for equivalent Rich/JSON/TOON documents; it adds no serializer, renderer hierarchy, live monitor, or execution abstraction.

### D6. Blocker repair remains planning-owned

The dependency and re-research conditions are satisfied: MYL-272 is integrated by PR #110 merge `11ff3403f2108adc901154ebeb9ee509add46ef5` and is present in selected base `c1e57b79f39e529a50c25818134c06309384ee23`; complete `multica-py` #93 is integrated by PR #95 merge `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed`; their public code, types, tests and version metadata were re-read for this revision.

The implementation feature branch contains accepted predecessor work, while `WP-04` is blocked on task 4.2 and candidate `c15b84d9bc87a63513d9da04f8718dc3cdd3e0cc` is unaccepted. The public Project-resource contract above is the planning-approved repair. `WP-04` remains blocked until this exact planning SHA is strictly validated, pushed unchanged, and independently approved by Plan Verifier; Manager or WP Delivery SHALL NOT alter production code or OpenSpec to bypass that gate. Any later incompatible dependency, base, scope, estimate-threshold, or topology drift returns to planning.

## Risks / Trade-offs

- **`multica-py` still declares an untagged `0.1.0`** → pin the verified exact revision in lock/compatibility evidence until a unique equivalent release exists; reject ambiguous artifacts.
- **Native checkout may retain dirty code or cached refs** → core first adoption rejects mismatched/dirty inputs without reset; an existing ready adoption is resolved before those first-use checks.
- **Task checkout can disappear during or after restore** → retain environment/recovery identity, diagnose missing code, and clean only independently proven SDK artifacts.
- **No extension-side persistent association** → the workflow caller stores context plus environment UUID; later telemetry attribution remains #105.
- **Cross-package compatibility can drift** → declare compatible versions, run installed-wheel tests, and fail before mutation on unsupported contracts.
- **Project resources may be incomplete or ambiguous** → require a complete public typed page and exactly one repository match; never fall back to TaskRun snapshots, prose, list order, or local guesses.
- **Live daemon/Odoo evidence is environment-sensitive** → require an explicitly approved disposable acceptance fixture; static research never substitutes for it.

## Migration Plan

1. Independently approve this task-4.2 blocker-revision exact SHA against the recorded feature head, base and dependency revisions.
2. Add the smallest ownership/project evidence migration and generic adoption path while preserving existing SDK-owned behavior.
3. Add the optional package using the observed typed Multica contracts and existing output boundary.
4. Validate fake boundaries, installed wheels, and an approved disposable native-daemon/Odoo flow before release.

Rollback uninstalls the extension without deleting environments. A compatible core cleans only proven SDK-owned artifacts by UUID. Catalog migration is forward-only; an older core must not manage rows carrying the new ownership evidence.

## Open Questions

No product or dependency-contract decision is delegated to implementation. Plan Verifier approval of this exact revision is the only remaining planning gate for resuming `WP-04`.
