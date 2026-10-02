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
2. The extension canonicalizes and verifies that path against the explicitly selected core project/repository and the typed issue/run/daemon context.
3. It requires same-host/shared-filesystem evidence and containment beneath the run's absolute current or durable directory; equal strings or reachability are insufficient.
4. Core adoption validates repository/HEAD/cleanliness on first use, persists explicit ownership evidence, and reuses the predecessor COPY pipeline.
5. Later core lifecycle uses the environment UUID. Multica continues to own code lifetime; core cleanup owns only independently proven SDK artifacts.

Project `local_directory` remains unsuitable because it is project/daemon-wide rather than issue-scoped. A binding registry remains unnecessary because the caller can retain the verified context plus environment UUID.

## Compatibility and packaging consequence

The observed `multica-py` revision declares package version `0.1.0` but has no containing release tag. The extension must therefore pin `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed` in dependency/lock evidence until a uniquely versioned equivalent distribution is available. An arbitrary `multica-py==0.1.0` is not sufficient identity. Installed-wheel and no-sources tests must prove that the extension uses only the public types above and a supported CLI (`>=0.5.3` for checkout).

The core base also declares `0.1.0`; implementation shall bind to the integrated base/release containing PR #110 rather than assume every artifact with that version has the required source/COPY behavior.

## Reconciliation result

- **Scope:** unchanged. The new evidence confirms the selected thin-extension plus generic-adoption design and removes provisional API discovery work; it adds no product capability.
- **Topology:** unchanged. Core adoption/catalog remains the shared foundation; core lifecycle and the extension package remain a real disjoint parallel frontier; integrated acceptance and final publication remain the fan-in.
- **Estimate threshold:** unchanged. Uncertainty is lower, but the remaining catalog/adoption/lifecycle/package/integration work still exceeds the authoritative multi-WP threshold. Exact totals live only in issue properties.
- **Issue #70 split:** unchanged. Context and preparation remain here; telemetry, usage allocation and inventory enrichment remain #105.

## Implementation authorization checklist

1. Strictly validate and push this complete post-dependency package at one exact SHA.
2. Verify the remote branch resolves to that exact SHA and the managed worktree is cleanly removed.
3. Obtain independent Plan Verifier approval of that SHA.
4. Only then may the parked implementation parent materialize/start the recorded WPs; any incompatible base, API, version or estimate-threshold drift returns to planning.
