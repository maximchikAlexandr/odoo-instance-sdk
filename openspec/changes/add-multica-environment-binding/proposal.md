## Why

The analysis workflow needs Multica's native task checkout and Odoo's isolated runtime to operate on the same code without creating a second checkout or registry. Both prerequisite implementations are now integrated, so this revision binds the design to their observed public typed Multica SDK and core source/COPY contracts instead of the earlier provisional raw-CLI assumptions.

## What Changes

- Deliver independently installed `odcli-multica` using public typed operations from the verified dependency revisions or equivalent uniquely versioned releases; core remains independent of Multica.
- Reuse `MulticaClient.repositories.checkout_command()/checkout()` returning `RepositoryCheckoutResult` and `MulticaClient.daemon.status_command()/status()` returning `DaemonStatus`, as delivered by `multica-py` #93. Do not retain `cli.command_command()` calls or local checkout/status decoders.
- Add generic core caller-owned-checkout adoption, reusing COPY provisioning while separating code ownership, SDK artifact ownership, configured project identity, and actual checkout identity.
- Expose finite read-only context and Odoo preparation operations. Native checkout and preparation remain two explicit phases; the caller retains their results.
- Reuse existing core lifecycle, diagnostics, retry, and owned-only cleanup surfaces. Do not add a binding registry, checkout registry, task dispatcher, or workflow engine.
- Publish this post-dependency planning revision against current `main`; implementation remains gated only on independent approval of its new exact SHA.

## Capabilities

### New Capabilities

- `multica-environment-binding`: Verified stateless task-to-environment handoff through native typed Multica operations and core adoption.

### Modified Capabilities

- `development-environment`: Caller-owned checkout adoption and ownership-aware lifecycle without Git creation or deletion.
- `packaging`: Independently installed extension with compatible public core and Multica SDK dependencies.

## Impact

Core changes affect environment planning/catalog evidence, COPY provisioning, lookup, runtime/config resolution, diagnostics, and cleanup. The extension lives in `packages/odcli-multica` and consumes public typed `multica-py` checkout, issue/run, and daemon-status contracts.

The dependency gates are satisfied by Odoo Instance SDK PR #110 merge `11ff3403f2108adc901154ebeb9ee509add46ef5`, present in the selected `main` base, and `multica-py` PR #95 merge `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed` (`multica-py` package version `0.1.0`, native CLI floor `0.5.3`). The remaining implementation gate is independent approval of this post-dependency exact planning SHA; the parked implementation parent and its WPs SHALL NOT start before that approval.

Telemetry/inventory enrichment remains in GitHub #105. Project CRUD, persistent binding, business workflow, skills, Temporal workers, reports, dashboard, billing, access-policy implementation, and automatic package/daemon installation remain out of scope.
