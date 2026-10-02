# Checkout integration research

## Post-dependency evidence baseline — 2026-10-02

This is source and contract inspection, not a live Multica/Odoo acceptance run. No application task, runtime, project resource, checkout, database, or daemon lifecycle was mutated.

| Source | Inspected revision/state | Observed contract |
|---|---|---|
| Odoo Instance SDK implementation base | `c1e57b79f39e529a50c25818134c06309384ee23` | Current `main`; includes the completed MYL-272 implementation and subsequent integrated changes. |
| MYL-272 / PR #110 | merge `11ff3403f2108adc901154ebeb9ee509add46ef5`; issue `done` | Named-source and exact retained-backup COPY selection, provenance, restoration, readiness, retention and owned cleanup are integrated. |
| `multica-py` #93 / PR #95 | merge `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed`; issue closed | Complete public CLI parity is integrated, including typed native checkout and full daemon status. |
| `multica-py` package metadata | version `0.1.0`; no containing release tag at inspected revision | Exact revision pin is required until an equivalent uniquely versioned distribution exists. |
| Native Multica CLI compatibility | checkout step minimum `0.5.3` | Compatibility is enforced by the SDK command plan; the extension does not reproduce it. |

The former dependency gates are complete. Implementation remains closed only until this post-dependency exact planning SHA is independently approved.

## Task 4.2 blocker re-research — 2026-10-02

The implementation feature head inspected for this revision is `8f63f6d07ac2e9947dbf190590293266e2dd29ae`. Its accepted predecessor WPs already implement the core adoption/lifecycle and extension package. The unaccepted task-4.2 candidate `c15b84d9bc87a63513d9da04f8718dc3cdd3e0cc` is not merged into that head. Live acceptance exposed one planning-contract mismatch: the selected TaskRun can have no `project_id` and an empty `project_resources` snapshot even though authoritative project and repository records exist.

The current platform public surface returns the planning issue with project id `be21ace8-af16-4e39-a10a-dbec46d6b2ad`; `multica project resource list` returns one `github_repo` record (`f27b41c9-d3bf-48b0-add2-8f4b4f9eb20d`) whose structured URL is the selected repository. At exact `multica-py` revision `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed`, `MulticaClient.projects.resources.list_command(project_id)` / `list(project_id)` return public typed `Command[Page[ProjectResourceRecord]]` / `Page[ProjectResourceRecord]`; each record exposes `project_id`, `resource_type`, and typed `resource_ref`, including `GithubRepoResourceRef.url`. By contrast, `Project.resources` returns `LazyCollection[ProjectResourceRecord]`, and its relation loader calls `resources.list(pid).items`, so that convenience relation does not retain `Page.has_more`, `total`, `offset`, or `next_cursor` for caller verification.

The minimal supported contract is therefore:

1. `Issue.project_id` SHALL match the explicitly selected `Project.id`.
2. The direct public `projects.resources.list_command()/list()` page SHALL have `has_more is False`, no `next_cursor`, no nonzero `offset`, a present `total` equal to `len(items)`, and exactly one `github_repo` match for the normalized selected core Git origin. `Project.resources` SHALL NOT be used as completeness evidence.
3. TaskRun SHALL still prove the exact issue/run, workspace, runtime and absolute current/durable task path; daemon status SHALL still prove same-host/shared-filesystem identity.
4. Populated TaskRun `project_id` or `project_resources` SHALL agree with authoritative Issue/Project evidence, but an omitted duplicate snapshot SHALL NOT fail that otherwise complete proof.
5. Incomplete pagination, no match, multiple matches, identity conflict, or missing run/daemon/path proof SHALL fail before mutation.

Requiring the platform to populate the duplicated TaskRun snapshot was rejected: it adds a prerequisite while providing no stronger identity than the supported source-of-truth Issue/Project relations. Reading raw CLI JSON, private transport, task prose, list order, branch names, or `local_directory` was also rejected. The chosen repair reuses one existing public relation and does not add a registry, adapter, decoder, or fallback identity source.

## Task 4.2 native fixture re-research — 2026-10-02

The subsequent unaccepted WP-04 candidate is `8c912c81d6a21d4675b11a3c87a44884a14620c2` on `origin/wp/MYL-307-WP-04-integrated-acceptance`. Its live selector is skipped unless a caller supplies an executable, repository/project/issue/run/task-root identity, Odoo executable, evidence root, and exactly one of `ODCLI_MULTICA_REMOTE_NAME` or `ODCLI_MULTICA_BACKUP_ID`. The project currently exposes only the SDK GitHub resource, and no approved disposable Odoo source exists. Therefore the skip is a real missing fixture contract, not successful native evidence.

The existing real-Odoo foundation supplies a reusable immutable source identity without changing product scope: public repository `https://github.com/odoo/odoo.git`, commit `cd992ceebbaf343c03e1941d39cfe423d35ba6c6`, audited CPython `3.12.13` dependency lock, loopback-only source/target patterns, owner-only secret files, unique per-run database names, cleanup ledgers, and sanitized evidence helpers. Its Compose tiers are useful provisioning evidence but do not exercise Multica's native checkout, the selected TaskRun/daemon identity, or a native target process from the borrowed checkout.

The safe live selector is the existing named-source contract, fixed to `EnvironmentCheckoutOptions(remote_name="disposable-native-source")`. The source is created for the run, initialized natively with only Odoo `base` and no demo data, and addressed through loopback with generated owner-only credentials. This reuses the public backup download/catalogue/provenance pipeline and avoids an unverifiable pre-existing retained backup UUID. Task 4.2 forbids `backup_id`, direct `source_database`, default fallback, customer sources, and catalogue rows that predate the fixture.

Repository authority requires a dedicated disposable Multica fixture project rather than the current SDK project: its sole `github_repo` resource identifies the pinned public Odoo repository, and its fixture issue creates the actual active TaskRun below whose owner-only absolute root typed checkout runs. A separate core-project clone of the same repository/ref carries the test-local `.odcli` manifest; the typed native checkout remains Multica-owned. Core cleanup removes only its target database, filestore, config/log/lock and other proven artifacts. Fixture teardown separately removes the native source process/database/filestore, generated PostgreSQL role and credentials, and core-project clone, while Multica retires its issue/project/checkout through public lifecycle.

This fixture is intentionally operator-provisioned rather than a new product orchestrator. WP-04 owns only test wiring, preflight, evidence and teardown. Fail-closed preflight precedes checkout/database mutation and verifies exact repository/ref, unique names, same-host daemon/runtime/path, owner-only task root and secret files, available pinned native executable/Python, and an empty cleanup ledger. Sanitized evidence records exact versions/ref/selector, identity checks, lifecycle phases, isolation and cleanup booleans/hashes; it never retains credentials, source dumps, customer identifiers, or private absolute paths.

## Native checkout RPC/cache prerequisite re-research — 2026-10-02

Candidate `ad6e11fa3e6bc2d5e51c9acf1d98fb99919eb87c` remains unaccepted. Two disposable fixture attempts used the public supported checkout path for `https://github.com/odoo/odoo.git` at `cd992ceebbaf343c03e1941d39cfe423d35ba6c6`. The second recorded exact command was `multica repo checkout https://github.com/odoo/odoo.git --ref cd992ceebbaf343c03e1941d39cfe423d35ba6c6 --fresh`. On native CLI `0.6.0`, the daemon reached `git fetch origin` and promisor `index-pack`, but the public checkout RPC returned `context deadline exceeded`; no checkout root or `.git` result was produced. An independent origin checkout proved the pinned commit exists upstream. Both disposable projects/issues were retired without product-data mutation.

The installed CLI's public help exposes only `--ref` and `--fresh` for `repo checkout`; it exposes no checkout-RPC timeout or cache-refresh flag. `multica-py` `OperationOptions.timeout` bounds its command execution but does not prove that the native CLI's internal HTTP deadline is extended. Repeating the timed-out command, relying on unobserved eventual cache warming, writing directly into the daemon bare cache, calling private daemon HTTP, or substituting an unrelated origin clone would all weaken the public ownership and result contracts.

The minimal executable planning contract is therefore an explicit platform prerequisite. Before the D7 acceptance fixture is created, the platform owner must prove the same daemon/cache can complete the supported checkout path for the exact Odoo repository/ref in a separate disposable probe TaskRun and return a successful path/result inside the public RPC deadline. The probe must prove exact commit equality, no surviving fetch/index-pack, and public-lifecycle cleanup. If current platform behavior cannot do this, a supported platform release or configuration with an RPC lifetime long enough for fetch/index-pack must be supplied first. Only after that certificate may WP-04 create the separate acceptance project/task and run its `fresh=False` typed checkout; a probe worktree itself is never reused as acceptance input.

This prerequisite preserves the original native lifecycle and two-domain cleanup. It moves daemon-cache readiness to the platform owner, where it belongs, while WP-04 still owns fixture checkout → context → COPY adoption → native start/status → stop/remove and proves both core-owned cleanup and fixture/Multica-owned retirement. External platform repair waiting is not developer effort and is excluded from the estimate, but readiness verification and acceptance reruns remain included.

## Observed Odoo Instance SDK contracts

`EnvironmentResource.checkout_command(project, branch, *, options=EnvironmentCheckoutOptions()) -> Command[DevelopmentEnvironment]`, `checkout()` and `checkout_with_plan()` remain the public SDK-owned checkout surfaces. `EnvironmentCheckoutOptions` now includes:

- `remote_name: str | None` for one configured named source;
- `backup_id: uuid.UUID | str | None` for one available retained catalogue backup;
- the existing explicit `base_ref`, COPY mode, source/target database, Python, config, port and lock inputs.

For COPY, `remote_name` and `backup_id` are mutually exclusive and each is mutually exclusive with `source_database`. Named-source resolution uses `DatabaseRefreshOptions(remote_name=...)`; retained selection requires a complete UUID and an available catalogue projection. The selected backup carries source name/base URL/database/source Git branch, digest and pin state. Checkout captures a plan, revalidates under lock, restores through the existing COPY pipeline, records provenance, and returns the existing frozen `DevelopmentEnvironment`.

The integrated predecessor already owns source selection, credentials, backup download/catalogue, provenance comparison, restore/neutralization, readiness, replacement recovery, retention and COPY cleanup. This change adds only caller-owned code adoption and the evidence needed to keep that code outside cleanup. It SHALL NOT add a second source selector, restore pipeline, readiness loop, retention implementation, or backup deletion policy.

Caller-owned adoption is still absent from current `main`: normal checkout plans a Git worktree and cleanup derives code ownership from the SDK-created layout. Independent native clones also do not share the configured project's Git common directory. The required remaining core seam is therefore unchanged: bypass only Git acquisition, retain explicit configured-project plus actual-checkout identities, and reuse post-acquisition provisioning and lifecycle.

## Observed `multica-py` contracts

At PR #95 merge revision, `MulticaClient.repositories` is `RepositoryResource` and exposes:

- `checkout_command(url, *, ref=None, fresh=False, options=None) -> Command[RepositoryCheckoutResult]`;
- `checkout(url, *, ref=None, fresh=False, options=None) -> RepositoryCheckoutResult`;
- frozen `RepositoryCheckoutResult(path: str)`, also implementing `__str__` and `__fspath__`.

The command executes native `repo checkout`, validates a nonblank URL/ref and nonempty returned path, and declares CLI minimum `0.5.3`. This integration leaves `fresh=False` so it never discards retained native work.

`MulticaClient.daemon` is `DaemonResource` and exposes:

- `status_command(*, options=None) -> Command[DaemonStatus]`;
- `status(*, options=None) -> DaemonStatus`.

Frozen `DaemonStatus` includes status/PID/uptime, OS/profile, daemon/device/server/CLI identity, launch identity, task and maintenance counters, agent projections, reload reason and typed workspace projections. The legacy optional `running` field exists only for old fixture decoding and is not the primary health contract.

Both resources consume `OperationOptions(profile, workspace_id, timeout, cwd, environment)`, preserve inspectable `Command.commands`, and delegate execution to `Command.run()`. Public failures include compatibility, timeout, cancellation and execution errors. A timeout or cancellation cannot prove that native checkout had no effect, so preparation remains a separate explicit phase and never begins automatically after such a failure.

No raw `CliResource.command_command()`, stdout/status decoder, private transport, HTTP call, or copied process runner is required or allowed.

## Native checkout and context boundary

1. Native repository checkout remains task-owned and returns its path through `RepositoryCheckoutResult`.
2. The extension canonicalizes and verifies that path against the explicitly selected core project/repository and the typed Issue/Project-resource plus run/daemon context.
3. It requires same-host/shared-filesystem evidence and containment beneath the run's absolute current or durable directory; equal strings or reachability are insufficient.
4. Core adoption validates repository/HEAD/cleanliness on first use, persists explicit ownership evidence, and reuses the predecessor COPY pipeline.
5. Later core lifecycle uses the environment UUID. Multica continues to own code lifetime; core cleanup owns only independently proven SDK artifacts.

Project `local_directory` remains unsuitable because it is project/daemon-wide rather than issue-scoped. A binding registry remains unnecessary because the caller can retain the verified context plus environment UUID.

## Compatibility and packaging consequence

The observed `multica-py` revision declares package version `0.1.0` but has no containing release tag. The extension must therefore pin `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed` in dependency/lock evidence until a uniquely versioned equivalent distribution is available. An arbitrary `multica-py==0.1.0` is not sufficient identity. Installed-wheel and no-sources tests must prove that the extension uses only the public types above and a supported CLI (`>=0.5.3` for checkout).

The core base also declares `0.1.0`; implementation shall bind to the integrated base/release containing PR #110 rather than assume every artifact with that version has the required source/COPY behavior.

## Reconciliation result

- **Scope:** unchanged. The blocker repair substitutes the public authoritative Issue/Project-resource relation for an optional duplicated TaskRun snapshot and makes the already-required live fixture executable; it adds no product capability or production orchestrator.
- **Topology:** unchanged. Core adoption/catalog remains the shared foundation; core lifecycle and the extension package remain a real disjoint parallel frontier; integrated acceptance and final publication remain the fan-in.
- **Estimate threshold:** must be recomputed from the complete revision. Accepted predecessor work lowers remaining effort, while dedicated project/run provisioning, native source/target setup, fail-closed preflight and cleanup evidence make the live acceptance boundary explicit. Exact totals live only in issue properties.
- **Issue #70 split:** unchanged. Context and preparation remain here; telemetry, usage allocation and inventory enrichment remain #105.

## Implementation authorization checklist

1. Strictly validate and push this complete post-dependency package at one exact SHA.
2. Verify the remote branch resolves to that exact SHA and the managed worktree is cleanly removed.
3. Obtain independent Plan Verifier approval of that SHA.
4. Only then may Plan Verifier update the graph contract and Manager resume blocked `WP-04`; any incompatible base, API, version or estimate-threshold drift returns to planning.
