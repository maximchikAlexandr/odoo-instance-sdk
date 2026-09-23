## Why

`odcli env checkout --db-mode copy` currently calls the source Database Manager directly, so checkout fails when the configured source Odoo is stopped even though the project already contains enough runtime configuration to start it safely. COPY checkout should be self-contained while preserving strict process ownership and the existing database-copy recovery guarantees.

## What Changes

- Reuse the existing bounded auxiliary Database Manager lifecycle for COPY checkout instead of requiring a manually started source Odoo.
- Reuse an already responsive source Database Manager without claiming ownership or stopping its process.
- Start source Odoo only when the endpoint is unavailable and the configured port is proven free; fail closed for an occupied, unhealthy, or unowned listener.
- Include auxiliary start, readiness, and cleanup in the same immutable checkout command plan, while keeping dry-run non-mutating.
- Clean up only the exact auxiliary process and temporary secret configuration owned by the checkout after success, failure, timeout, or cancellation without masking the primary error.
- Preserve the current COPY journal, target-absence checks, backup provenance, restore postconditions, and compensating cleanup behavior; leave shared database mode unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-restore`: Extend COPY environment checkout so its source Database Manager lifecycle is self-contained, ownership-safe, visible in the captured plan, and always cleaned up.

## Impact

- Affected implementation: environment checkout command composition and the existing instance auxiliary-restore attachment/session boundary.
- Affected tests: focused auxiliary lifecycle tests, COPY checkout command/rollback tests, dry-run plan coverage, and shared-mode regression coverage.
- Public CLI syntax, persisted storage schema, dependencies, and shared database mode remain unchanged.
