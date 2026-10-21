## ADDED Requirements

### Requirement: Auxiliary Database Manager has an independent endpoint
When restore needs to spawn an auxiliary Odoo, its immutable launch snapshot SHALL capture a separate available loopback HTTP port using the existing allocator, excluding the source instance port. Readiness, database-list, backup and restore HTTP requests for that session SHALL use the captured helper endpoint. The source project configuration SHALL remain unchanged. An exactly proven recorded runtime MAY still be reused under existing identity checks.

The selected helper port SHALL be revalidated before spawn. Password-bearing requests SHALL retain exact listener ownership checks for the selected runtime. Cleanup SHALL stop only the helper process owned by the operation, never the primary or another listener.

#### Scenario: Primary listener remains running
- **WHEN** the primary HTTP port is occupied and its runtime cannot be proven reusable
- **THEN** restore starts its helper on a different loopback port and routes session requests there without stopping or modifying the primary

#### Scenario: Captured helper port becomes occupied
- **WHEN** another listener takes the selected helper port after planning
- **THEN** execution fails before spawn or password-bearing HTTP and leaves that listener and the primary untouched

#### Scenario: Helper identity is lost
- **WHEN** the helper exits or no longer owns its selected listener before a password-bearing request
- **THEN** the request is rejected under the existing failure/cleanup contract
