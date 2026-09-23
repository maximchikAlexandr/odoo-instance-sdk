## Why

COPY checkout currently requires the source Odoo Database Manager to be running even when the SDK has enough project configuration to start it safely. A stopped source therefore makes an otherwise valid local copy fail, while the adjacent project-restore flow already proves an owned, bounded auxiliary Odoo lifecycle.

## What Changes

- Make COPY checkout probe the configured local source Database Manager before durable checkout mutation.
- When that probe reports `DatabaseManagerUnavailableError`, start a temporary source Odoo from the captured project runtime and source configuration, wait for Database Manager readiness, retry the source operation, and stop only the process started by this checkout.
- Reuse a healthy runtime whose ownership is proven for the same project; fail closed on foreign listeners, unsafe or incomplete runtime configuration, startup/readiness failure, or cleanup failure.
- Include the conditional auxiliary start, readiness, and cleanup operations in the immutable checkout command plan while keeping dry-run non-mutating and secret-safe.
- Preserve the existing COPY backup, target-absence, restore, postcondition, journal, rollback, provenance, and public API contracts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `development-environment`: COPY checkout becomes self-sufficient when its local source Database Manager is stopped, with bounded ownership, plan visibility, and cleanup guarantees.

## Impact

- Affected implementation: environment checkout planning/execution, source database backup/list calls, and the existing auxiliary Database Manager lifecycle under `src/odoo_instance_sdk/resources/`.
- Affected tests: COPY checkout command-plan and lifecycle regressions, including stopped source, healthy owned runtime reuse, foreign listener, startup/readiness failure, cleanup, and dry-run coverage.
- Public method signatures and dependencies remain unchanged; the behavior is additive for local COPY checkout.
