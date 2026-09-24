## Why

At implementation review revision `46efd7d882fa122120e149896d560340cc96777b`, `odcli env checkout --db-mode copy` already uses a bounded auxiliary lifecycle to start a stopped source Odoo and clean up owned state. Review found that recorded process identity is not yet bound to the live listening socket, privileged source backup can bypass request-adjacent identity revalidation, and the port precondition is absent from the immutable plan. COPY checkout must close those trust and observability gaps without weakening its existing self-contained lifecycle or database-copy recovery guarantees.

## What Changes

- Harden the bounded auxiliary Database Manager lifecycle already integrated into COPY checkout.
- Reuse only a recorded source runtime whose exact live process identity and ownership of the configured listening socket are proven; a responsive but unrecorded listener fails closed.
- Start source Odoo only when the configured port is proven free; fail closed for an occupied, unhealthy, unowned, or unverifiable listener.
- Revalidate the recorded process-to-listener binding immediately before every auxiliary Database Manager request that carries the master password, and send no secret when that proof fails.
- Include auxiliary identity, port/ownership, privileged-request revalidation, start, readiness, and cleanup actions in the same immutable checkout command plan, while keeping dry-run non-mutating.
- Clean up only the exact auxiliary process and temporary secret configuration owned by the checkout after success, failure, timeout, or cancellation without masking the primary error.
- Preserve the current COPY journal, target-absence checks, backup provenance, restore postconditions, and compensating cleanup behavior; leave shared database mode unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-restore`: Harden the existing self-contained COPY source Database Manager lifecycle so endpoint trust is socket-bound, privileged-request-safe, visible in the captured plan, and ownership-clean.

## Impact

- Affected implementation: environment checkout command composition, the existing instance auxiliary-restore attachment/session boundary, and the Database Manager backup/restore request boundary used while an auxiliary session is active.
- Affected tests: focused process/socket identity and secret non-disclosure tests, auxiliary lifecycle tests, COPY checkout command/rollback tests, dry-run plan coverage, and shared-mode regression coverage.
- Public CLI syntax, persisted storage schema, dependencies, and shared database mode remain unchanged.
