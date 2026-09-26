## Why

The analysis workflow needs Multica's native task checkout and Odoo's isolated runtime to operate on the same code without creating a second checkout or registry. The integration must consume the final public typed Multica SDK and core source/COPY contracts rather than freeze temporary raw-CLI workarounds while those contracts are still being implemented.

## What Changes

- Deliver independently installed `odcli-multica` using public typed operations from compatible `multica-py` and Odoo Instance SDK releases; core remains independent of Multica.
- Reuse the native typed Multica checkout and daemon-status operations delivered by `multica-py` #93. Do not retain `cli.command_command()` calls or local checkout/status decoders as the product design.
- Add generic core caller-owned-checkout adoption, reusing COPY provisioning while separating code ownership, SDK artifact ownership, configured project identity, and actual checkout identity.
- Expose finite read-only context and Odoo preparation operations. Native checkout and preparation remain two explicit phases; the caller retains their results.
- Reuse existing core lifecycle, diagnostics, retry, and owned-only cleanup surfaces. Do not add a binding registry, checkout registry, task dispatcher, or workflow engine.
- Separate planning readiness from implementation readiness. Planning proceeds now; implementation remains gated until both prerequisite implementations are complete and this package is re-researched, updated, strictly validated, reviewed, and published at a new exact SHA.

## Capabilities

### New Capabilities

- `multica-environment-binding`: Verified stateless task-to-environment handoff through native typed Multica operations and core adoption.

### Modified Capabilities

- `development-environment`: Caller-owned checkout adoption and ownership-aware lifecycle without Git creation or deletion.
- `packaging`: Independently installed extension with compatible public core and Multica SDK dependencies.

## Impact

Core changes affect environment planning/catalog evidence, COPY provisioning, lookup, runtime/config resolution, diagnostics, and cleanup. The extension lives in `packages/odcli-multica` and consumes public typed `multica-py` checkout, issue/run, and daemon-status contracts.

Implementation has two mandatory gates: (1) MYL-271 planning plus the verified, integrated MYL-272 implementation of the consumed core source/COPY contracts; and (2) complete implementation of `multica-py` #93 with an available supported revision/version. Closing documentation alone, cancelling either dependency, or implementing only checkout/status does not open the gate. Once both are complete, the actual APIs and versions SHALL be inspected, all affected artifacts SHALL be revised, and a new exact verified SHA SHALL be published before any implementation work starts.

Telemetry/inventory enrichment remains in GitHub #105. Project CRUD, persistent binding, business workflow, skills, Temporal workers, reports, dashboard, billing, access-policy implementation, and automatic package/daemon installation remain out of scope.
