## MODIFIED Requirements

### Requirement: Public execution model vocabulary

The SDK SHALL publicly export lazy-loaded `Command[T]`, `ExecutionPlan`, frozen `ProcessStep`, frozen `ActionStep`, concrete plan errors, and `StalePlanError`. The public plan/value models SHALL be immutable, strictly typed, serializable through the project model boundary, and free of Expression or private executor types. A deadline-bound status observation MAY be plan-visible as private frozen serializable metadata, but SHALL NOT add a public model or remaining-budget API; it SHALL contain no per-run monotonic timestamp or executor state. `Command[T]` SHALL be immutable and strictly typed, but its private executable callback and snapshot SHALL NOT be serializable or included in project model conversion; only its public plan projection may be converted.

#### Scenario: Public execution imports

- **WHEN** a caller imports each execution model from `odoo_instance_sdk` or its canonical module
- **THEN** both imports return the same public object
- **AND** constructing or inspecting the models requires no private executor import through the package root

#### Scenario: Command model conversion is requested

- **WHEN** a caller converts public execution values through the project model boundary
- **THEN** `ExecutionPlan`, `ProcessStep`, `ActionStep`, and other public plan/value models produce serializable values
- **AND** the `Command[T]` private callback and executable snapshot are neither traversed nor emitted
