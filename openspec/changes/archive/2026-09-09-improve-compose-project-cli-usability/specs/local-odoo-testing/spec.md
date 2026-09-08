## ADDED Requirements

### Requirement: Module-test port preflight matches reusable local bind semantics

The shared local address probe used by module-test preflight SHALL test the bind semantics used by the next local Odoo listener, including setting `SO_REUSEADDR` before bind on supported platforms. A recently closed accepted loopback connection SHALL become `FREE` within the existing short bounded probe; an actively listening socket SHALL remain `OCCUPIED`; resolution or socket failures that cannot establish either state SHALL remain `UNKNOWN`. The preflight SHALL add no unconditional sleep, long retry, or weakening of live-listener detection.

#### Scenario: Recently closed connection is reusable

- **WHEN** a loopback listener accepts one connection and both accepted connection and listener are closed before an immediate probe
- **THEN** the address is classified `FREE` quickly enough for the following module-test preflight to proceed without a manual retry

#### Scenario: Active listener remains occupied

- **WHEN** a real listener is still bound and listening on the selected address
- **THEN** the probe returns `OCCUPIED` and module tests do not start

#### Scenario: Unknown probe fails closed

- **WHEN** address resolution or socket creation cannot determine bind availability
- **THEN** the probe returns `UNKNOWN` and the existing preflight does not execute Odoo tests
