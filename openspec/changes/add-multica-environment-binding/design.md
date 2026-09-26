## Context

Multica's project/daemon-wide `local_directory` cannot select one checkout per issue. Native checkout already owns the task-to-code association, while Odoo Instance SDK currently assumes that it created and may remove each environment worktree. The integration therefore needs one generic core adoption primitive and a thin optional package, not another checkout or binding registry.

The input package used public raw `cli.command_command()` calls and local decoders because typed checkout and complete daemon status were absent. `multica-py` #93 now explicitly requires full public CLI-to-typed-SDK parity, including native checkout and complete daemon-status contracts. Those temporary adapters are no longer an acceptable target design. Their final signatures are intentionally not guessed before #93 is implemented.

Two external implementations remain in flight: MYL-272 supplies the core source/COPY contracts planned by MYL-271, and `multica-py` #93 supplies the typed Multica contracts. They block implementation, not planning.

## Goals / Non-Goals

**Goals:**

- Prepare one isolated Odoo COPY environment against one native Multica checkout.
- Use only public typed dependency contracts and the repository's existing command/execution/output boundaries.
- Preserve explicit ownership, deterministic retry, diagnostics, and owned-only cleanup.
- Make planning readiness and implementation readiness independently auditable.

**Non-Goals:**

- Persistent binding or checkout registries, project-resource mutation, task routing, workflow scheduling, telemetry allocation, remote policy enforcement, or a second runtime manager.
- Reimplementation of Multica checkout/status behavior or preservation of raw CLI escape hatches for these operations.
- Inventing final dependency method names, result fields, or compatibility versions before the prerequisite code exists.

## Decisions

### D1. Native checkout and daemon identity use final typed `multica-py` operations

At implementation start, select the confirmed public typed native-checkout and daemon-status operations from the available revision/version that completes all of `multica-py` #93. Use their inspectable command siblings, typed results, scoped server/workspace configuration, timeout/cancellation, and redaction contracts directly.

The extension SHALL NOT call `cli.command_command()` for checkout or daemon status, parse checkout stdout, decode daemon JSON, access private HTTP/storage, or copy a subprocess runner. Checkout remains a separate phase from Odoo preparation. Timeout/cancellation retains the dependency's typed unknown-outcome semantics; the integration never assumes that no checkout was created. Forced fresh checkout is excluded.

Alternative rejected: keep the two local decoders as compatibility fallbacks. That creates two contracts for the same public operations and would preserve precisely the parity gap #93 is required to close.

### D2. Explicit read-only context, no configuration CRUD

Provide a small integration client with read-only `context` and preparation operations. Inputs identify the selected core project/repository, exact checkout path, expected Multica project, issue, and run. Server/workspace/credentials come from the scoped Multica client. Repository identity comes from the explicitly selected core project; ambiguity fails.

Context composes typed issue/run and daemon-status operations to verify workspace/project/issue/run membership, the owning runtime/daemon, repository identity, and containment of the actual Git root beneath an absolute current/durable task directory. Missing fields, incomplete pagination, conflicting identities, relative-only paths, or forwarded/container endpoints without proven shared-filesystem evidence fail before mutation. The frozen result records verified identifiers, checkout facts, and observation time, but is not a lease.

Alternative rejected: infer identity from task prose, branch names, path equality, or project `local_directory`. None proves issue-scoped ownership on the local host.

### D3. Preparation delegates to generic core adoption

Add core caller-owned-checkout adoption as an inspectable command plus delegating convenience operation. It accepts COPY only, one explicit supported COPY source, an explicit compatible base, and an existing canonical Git checkout. It supports linked worktrees and independent clones without creating, moving, renaming, resetting, or deleting code.

First adoption verifies repository identity, path identity, branch/HEAD, clean status, manifest, source provenance, and locally resolved base before mutation. A ready matching record is resolved before first-adoption-only clean/base checks so ordinary later development returns the same UUID without another restore. Conflicting inputs and incomplete state return typed conflict/recovery evidence.

The integration performs bounded context preflight, captures the exact core adoption command, and returns the existing environment result. Context and environment UUID remain separate caller-owned workflow results. No composite continuation framework is introduced.

### D4. Code ownership is independent from SDK artifact ownership

Persist only the additive evidence required to distinguish configured core project identity, actual checkout identity, code ownership (`sdk_owned`, `caller_owned`, `unknown`), and explicit SDK artifact root. Generated config/log/lock/optional venv, COPY database, and isolated filestore remain under existing SDK ownership rules. Repository-local paths are rebased into the adopted checkout; external configured paths stay external; source `.env` is never copied.

List/cwd/config/sync/runtime/diagnostic/remove paths use recorded project and checkout evidence rather than guessing from Git common dir or `worktree_path.parent`. Rollback/removal never resets, prunes, renames, or recursively deletes caller-owned code, even when dirty, absent, or replaced. Legacy unknown ownership never grants deletion rights.

### D5. Packaging and output stay narrow

Add `packages/odcli-multica` as an independently versioned distribution/import/executable using the shared workspace scaffold and compatible published dependencies. Core does not import Multica. The extension uses existing public bounded output contracts for equivalent Rich/JSON/TOON documents; it adds no serializer, renderer hierarchy, live monitor, or execution abstraction.

### D6. Planning and implementation have separate readiness gates

This revision may complete OpenSpec validation, estimation, review, and publication while dependencies are unfinished. It is planning-ready only.

Implementation readiness requires all of the following evidence:

1. MYL-271 is closed and MYL-272 is complete, independently verified, integrated into the implementation base, and exposes the source/COPY contracts actually consumed here.
2. `multica-py` #93 is fully implemented—not cancelled or partially closed—and an exact supported revision/version containing the complete approved parity scope is available.
3. A Planner re-reads both implementations, their public API/types/tests/version metadata, and the current repository base; replaces every provisional contract reference with observed facts; revises proposal, research, design, specs, tasks, and delivery plan as needed; reruns estimation if scope/evidence changed; and publishes a new exact SHA.
4. Plan Verifier independently approves that new SHA through the normal human-review gate.

The implementation parent may exist in backlog with these gates recorded, but no WP child or implementation run may start before all four conditions are satisfied.

## Risks / Trade-offs

- **Dependency signatures are not yet available** → describe required semantics only now; make observed API/version capture and a new exact SHA mandatory before implementation.
- **Native checkout may retain dirty code or cached refs** → core first adoption rejects mismatched/dirty inputs without reset; an existing ready adoption is resolved before those first-use checks.
- **Task checkout can disappear during or after restore** → retain environment/recovery identity, diagnose missing code, and clean only independently proven SDK artifacts.
- **No extension-side persistent association** → the workflow caller stores context plus environment UUID; later telemetry attribution remains #105.
- **Cross-package compatibility can drift** → declare compatible versions, run installed-wheel tests, and fail before mutation on unsupported contracts.
- **Live daemon/Odoo evidence is environment-sensitive** → require an explicitly approved disposable acceptance fixture; static research never substitutes for it.

## Migration Plan

1. After both external dependencies complete, perform the mandatory API/version re-research and publish the new reviewed planning SHA.
2. Add the smallest ownership/project evidence migration and generic adoption path while preserving existing SDK-owned behavior.
3. Add the optional package using the observed typed Multica contracts and existing output boundary.
4. Validate fake boundaries, installed wheels, and an approved disposable native-daemon/Odoo flow before release.

Rollback uninstalls the extension without deleting environments. A compatible core cleans only proven SDK-owned artifacts by UUID. Catalog migration is forward-only; an older core must not manage rows carrying the new ownership evidence.

## Open Questions

No product decision is delegated to implementation. The only unresolved facts are the exact final public names, result types, and compatible versions produced by the two dependencies; D6 requires observing and recording them before implementation authorization.
