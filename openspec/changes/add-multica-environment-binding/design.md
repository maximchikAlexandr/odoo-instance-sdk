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

The native CLI `0.6.0` public `repo checkout` surface exposes `--ref` and `--fresh`, but no checkout-RPC deadline or cache-refresh control. A supported checkout that reaches daemon `git fetch`/promisor `index-pack` and then returns `context deadline exceeded` is therefore a platform-owned unknown outcome, not a fixture failure that WP code may repair. Task 4.2 SHALL require the D7 platform-readiness certificate below; increasing only `OperationOptions.timeout`, invoking private HTTP, or mutating the daemon bare cache directly is not a supported recovery path.

Alternative rejected: keep the two local decoders as compatibility fallbacks. That creates two contracts for the same public operations and would preserve precisely the parity gap #93 is required to close.

### D2. Explicit read-only context, no configuration CRUD

Provide a small integration client with read-only `context` and preparation operations. Inputs identify the selected core project/repository, exact checkout path, expected Multica project, issue, and run. Server/workspace/credentials come from the scoped Multica client. Repository identity comes from the explicitly selected core project; ambiguity fails.

Context fetches the expected project and issue, requires `Issue.project_id` to equal the selected `Project.id`, and calls public typed `MulticaClient.projects.resources.list_command(project.id)` / `list(project.id)` returning `Page[ProjectResourceRecord]`. Completeness requires `has_more is False`, no `next_cursor`, no nonzero `offset`, a present `total`, and `total == len(items)`. Exactly one matching `github_repo` item with a typed `GithubRepoResourceRef.url` then proves authoritative repository membership against the selected core Git origin. `Project.resources` is only a `LazyCollection[ProjectResourceRecord]` convenience relation whose loader consumes `.items`; it SHALL NOT supply pagination evidence. Missing, incomplete, ambiguous, or mismatched project-resource evidence fails before mutation.

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

The implementation feature branch contains accepted predecessor work, while `WP-04` is blocked on task 4.2 and candidate `ad6e11fa3e6bc2d5e51c9acf1d98fb99919eb87c` is unaccepted. The public Project-resource contract above, disposable fixture contract, and platform-readiness prerequisite below are the planning-approved repair. `WP-04` remains blocked until this exact planning SHA is strictly validated, pushed unchanged, and independently approved by Plan Verifier; Manager or WP Delivery SHALL NOT alter production code or OpenSpec to bypass that gate. Any later incompatible dependency, base, scope, estimate-threshold, or topology drift returns to planning.

### D7. Task 4.2 uses one prescribed disposable native fixture

Before provisioning the acceptance fixture, the platform owner SHALL issue a checkout-readiness certificate for the same native daemon and repository cache. In a separate disposable probe project/issue/TaskRun on that daemon, the supported public `multica repo checkout https://github.com/odoo/odoo.git --ref cd992ceebbaf343c03e1941d39cfe423d35ba6c6 --fresh` path (or the equivalent typed public operation from a uniquely identified compatible release) SHALL return success within its public RPC deadline, yield the exact pinned commit beneath the probe TaskRun root, and leave no running fetch/index-pack after completion. The probe worktree and task/project SHALL then be retired through public lifecycle. The certificate SHALL record sanitized daemon/runtime/CLI identity, exact repository/ref, command outcome, elapsed-bound result, commit equality, and cleanup; it SHALL NOT expose private paths or authorize direct bare-cache mutation. A timed-out call, eventual unobserved cache warming, a separately cloned origin, or a second retry without a successful response SHALL NOT satisfy readiness.

If CLI/platform behavior cannot meet that certificate, the platform owner SHALL first provide a supported release or configuration in which the checkout RPC remains valid through the required fetch/index-pack and returns the typed result. `OperationOptions.timeout` alone SHALL NOT be treated as proof that the native CLI HTTP deadline changed. Until the certificate exists, task 4.2 SHALL fail closed before creating the acceptance project, checkout, role, database, filestore, credentials, or core environment; `WP-04` remains blocked and no product-code workaround is authorized.

The fixture owner SHALL provision a dedicated temporary Multica project in the acceptance workspace with exactly one `github_repo` resource for `https://github.com/odoo/odoo.git`, a temporary issue assigned only to the acceptance executor, and one resulting active TaskRun on the same native daemon/filesystem as the test. The run's absolute current or durable directory SHALL be an owner-only (`0700`) disposable root. The typed checkout SHALL use `fresh=False`, the repository URL above, and ref `cd992ceebbaf343c03e1941d39cfe423d35ba6c6`, matching the repository's existing immutable real-Odoo pin. It SHALL create the checkout below that TaskRun root; a pre-existing checkout, another project/run, a forwarded daemon, or a customer workspace is not an admissible substitute.

The fixture owner SHALL prepare a separate owner-only core-project clone of the same repository at the same commit and a test-local Odoo/Python environment from the checked repository's existing audited real-Odoo lock. It SHALL create a unique PostgreSQL role plus two unique databases: a source initialized natively with only `base`, no demo data, and a separate target selected by core COPY provisioning. A native loopback-only source Odoo process SHALL expose that source under the manifest name `disposable-native-source`; fixture-generated master/database credentials SHALL live only in owner-only files. Task 4.2 SHALL pass only `EnvironmentCheckoutOptions(remote_name="disposable-native-source")`. `backup_id`, `source_database`, default-source fallback, pre-existing catalogue rows, customer sources, and customer credentials are forbidden for this acceptance case.

The target Odoo process SHALL be started from the adopted native checkout through the public core instance lifecycle and the configured native executable/Python environment, then observed ready/running, stopped, and removed. A container MAY supply an isolated PostgreSQL dependency, but containerized or Compose Odoo smoke SHALL NOT satisfy native checkout, native daemon, context, adoption, start/status, stop, or removal evidence. Teardown SHALL run from `finally`: stop both Odoo processes, remove the target environment/database/filestore and source database/filestore/role, remove fixture credentials and the core-project clone, and verify that core cleanup did not remove the Multica-owned checkout. The Multica fixture issue/project and native checkout remain owned by the fixture owner/Multica and SHALL be retired through their public lifecycle after evidence capture, never by recursive test cleanup.

The fixture preflight SHALL fail before checkout or database mutation unless the current platform-readiness certificate matches the exact daemon/cache/repository/ref, the project/resource/run/daemon/path evidence is complete, the pinned Odoo ref and native executable are available, names are unique, the task root is owner-only, and the source/target cleanup ledger is empty. Sanitized evidence SHALL record hashes or booleans rather than secrets or absolute private paths, the readiness certificate, exact repository/ref and dependency revisions, the selected source name, TaskRun snapshot-omission case, complete direct Project-resource page, lifecycle phases, database/filestore isolation, and post-cleanup ownership results.

Alternative rejected: reuse the current SDK project/run and point at an unrelated Odoo source. Its authoritative repository resource identifies the SDK repository, so it cannot prove the selected Odoo checkout belongs to the issue project. Alternative rejected: use only the existing Compose smoke. It does not exercise native Multica checkout/daemon identity or the core native lifecycle from the borrowed checkout. Alternative rejected: select an arbitrary retained backup UUID. Its origin and customer-data safety cannot be proven by this fixture.

## Risks / Trade-offs

- **`multica-py` still declares an untagged `0.1.0`** → pin the verified exact revision in lock/compatibility evidence until a unique equivalent release exists; reject ambiguous artifacts.
- **Native checkout may retain dirty code or cached refs** → core first adoption rejects mismatched/dirty inputs without reset; an existing ready adoption is resolved before those first-use checks.
- **Task checkout can disappear during or after restore** → retain environment/recovery identity, diagnose missing code, and clean only independently proven SDK artifacts.
- **No extension-side persistent association** → the workflow caller stores context plus environment UUID; later telemetry attribution remains #105.
- **Cross-package compatibility can drift** → declare compatible versions, run installed-wheel tests, and fail before mutation on unsupported contracts.
- **Project resources may be incomplete or ambiguous** → require a complete public typed page and exactly one repository match; never fall back to TaskRun snapshots, prose, list order, or local guesses.
- **Live daemon/Odoo evidence is environment-sensitive** → use the single D7 fixture contract with pinned public source, fixture-only databases/credentials, fail-closed preflight, cleanup ledger, and sanitized evidence; static research or Compose smoke never substitutes for it.
- **Large public repositories can exceed the native checkout RPC deadline while daemon fetch/index-pack continues** → require a same-daemon supported checkout-readiness certificate or a platform release/configuration that completes the RPC; never mutate the bare cache directly or treat timeout/eventual warming as success.

## Migration Plan

1. Independently approve this task-4.2 blocker-revision exact SHA against the recorded feature head, base and dependency revisions.
2. Add the smallest ownership/project evidence migration and generic adoption path while preserving existing SDK-owned behavior.
3. Add the optional package using the observed typed Multica contracts and existing output boundary.
4. Obtain the D7 platform-readiness certificate on the same daemon/cache; only then provision the disposable acceptance project/run and native Odoo source, execute task 4.2 with `remote_name=disposable-native-source`, capture sanitized evidence, and complete both cleanup domains before release.

Rollback uninstalls the extension without deleting environments. A compatible core cleans only proven SDK-owned artifacts by UUID. Catalog migration is forward-only; an older core must not manage rows carrying the new ownership evidence.

## Open Questions

No product or dependency-contract decision is delegated to implementation. Plan Verifier approval of this exact revision and the externally supplied platform-readiness certificate are the remaining gates for resuming `WP-04`; platform repair waiting is not implementation work.
