## Why

The analysis workflow needs Multica's native task checkout and Odoo's isolated runtime to operate on the same code without creating a second checkout or registry. Both prerequisite implementations are now integrated, so this revision binds the design to their observed public typed Multica SDK and core source/COPY contracts instead of the earlier provisional raw-CLI assumptions.

## What Changes

- Deliver independently installed `odcli-multica` using public typed operations from the verified dependency revisions or equivalent uniquely versioned releases; core remains independent of Multica.
- Reuse `MulticaClient.repositories.checkout_command()/checkout()` returning `RepositoryCheckoutResult` and `MulticaClient.daemon.status_command()/status()` returning `DaemonStatus`, as delivered by `multica-py` #93. Do not retain `cli.command_command()` calls or local checkout/status decoders.
- Resolve authoritative project/repository identity through public typed `Issue.project_id`, `Project`, and `MulticaClient.projects.resources.list_command()/list()` page surfaces. Treat `Project.resources` only as a convenience collection without pagination evidence, and treat duplicated TaskRun project snapshots as optional consistency observations while retaining strict run/workspace/runtime/path and daemon checks.
- Add generic core caller-owned-checkout adoption, reusing COPY provisioning while separating code ownership, SDK artifact ownership, configured project identity, and actual checkout identity.
- Expose finite read-only context and Odoo preparation operations. Native checkout and preparation remain two explicit phases; the caller retains their results.
- Reuse existing core lifecycle, diagnostics, retry, and owned-only cleanup surfaces. Do not add a binding registry, checkout registry, task dispatcher, or workflow engine.
- Prove task 4.2 in one disposable same-host fixture: a dedicated Multica project/run whose sole repository resource is the public pinned Odoo repository, a native typed checkout inside that run, a native Odoo source and target backed only by fixture-created databases/filestores, and the exact named COPY selector `remote_name=disposable-native-source`. Compose smoke is not acceptance evidence for this flow.
- Gate that fixture on a platform-owned native-checkout readiness proof. The same daemon/cache SHALL first complete the supported checkout path for the pinned Odoo object in a separate disposable probe task within the public RPC deadline. Direct bare-cache mutation, private transport, and a timed-out fetch/index-pack are not readiness evidence; until the platform provides a supported release/configuration that completes the RPC, `WP-04` remains blocked before product or database mutation.
- Publish this task-4.2 blocker revision on the integration branch; `WP-04` remains blocked and its candidate remains unaccepted until independent approval of the new exact SHA.

## Capabilities

### New Capabilities

- `multica-environment-binding`: Verified stateless task-to-environment handoff through native typed Multica operations and core adoption.

### Modified Capabilities

- `development-environment`: Caller-owned checkout adoption and ownership-aware lifecycle without Git creation or deletion.
- `packaging`: Independently installed extension with compatible public core and Multica SDK dependencies.

## Impact

Core changes affect environment planning/catalog evidence, COPY provisioning, lookup, runtime/config resolution, diagnostics, and cleanup. The extension lives in `packages/odcli-multica` and consumes public typed `multica-py` checkout, issue/run, and daemon-status contracts. Acceptance additionally needs an operator-provisioned, owner-only, disposable Multica/Odoo fixture and a platform-owned supported checkout-readiness proof on its daemon/cache; it does not authorize customer projects, databases, credentials, retained backups, or direct cache repair.

The dependency gates are satisfied by Odoo Instance SDK PR #110 merge `11ff3403f2108adc901154ebeb9ee509add46ef5`, present in the selected `main` base, and `multica-py` PR #95 merge `c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed` (`multica-py` package version `0.1.0`, native CLI floor `0.5.3`). Foundation and package WPs are integrated on the feature branch, but the task-4.2 candidate is not accepted. `WP-04` SHALL remain blocked until independent approval of this blocker-revision exact SHA; no production or OpenSpec repair may bypass the planning flow.

Telemetry/inventory enrichment remains in GitHub #105. Project CRUD, persistent binding, business workflow, skills, Temporal workers, reports, dashboard, billing, access-policy implementation, and automatic package/daemon installation remain out of scope.
