## MODIFIED Requirements

### Requirement: One private executable snapshot

Command construction SHALL capture one immutable private executable snapshot. Before a command is made available, construction SHALL compare the public plan's complete ordered step sequence with the complete ordered public projection of the private snapshot and SHALL raise `PlanValidationError` when the sequences differ. `Command.run()` SHALL be repeatable: each invocation SHALL create an independent per-run consumption ledger over that same snapshot and SHALL share no consumption state with earlier, later, or concurrent invocations. Within one run, execution SHALL request each captured process step through its identifier exactly once. An unplanned, substituted, or duplicate request SHALL fail before the requested child starts. An omitted step SHALL fail the run when the operation callback completes and the ledger is checked; effects completed earlier in that run are not promised to be absent or rolled back unless the operation's existing lifecycle contract provides that guarantee.

#### Scenario: Preview and execution have parity

- **WHEN** a recording executor runs a previously inspected command
- **THEN** applying the production redaction function to each recorded executor input yields the corresponding public process step exactly

#### Scenario: Private snapshot has an unadvertised step

- **WHEN** command construction receives a private snapshot whose ordered public projection contains a step absent from the public plan
- **THEN** construction raises `PlanValidationError` before the command is registered or run

#### Scenario: Public plan advertises an absent executable step

- **WHEN** command construction receives a public plan whose ordered steps contain a step absent from the private snapshot projection
- **THEN** construction raises `PlanValidationError` before the command is registered or run

#### Scenario: Step projections differ without changing identifiers

- **WHEN** the public and private sequences use the same step identifiers but differ in order, step kind, or any projected step field
- **THEN** construction raises `PlanValidationError`

#### Scenario: Detached launch preview is complete

- **WHEN** a detached Odoo launch command is constructed
- **THEN** its public plan contains the same ordered preparation, process, port-validation, spawn, liveness-confirmation, and runtime-persistence step projections as its private snapshot
- **AND** construction performs none of those planned effects

#### Scenario: Operation attempts an unplanned launch

- **WHEN** operation lifecycle code requests a child command not present in the captured snapshot
- **THEN** execution fails before that child starts

#### Scenario: The same command runs twice

- **WHEN** a caller invokes `.run()` twice on one immutable command
- **THEN** each invocation starts with a fresh empty consumption ledger and executes the same captured snapshot
- **AND** consumption recorded by either invocation does not affect the other

#### Scenario: Operation callback omits a planned step

- **WHEN** an operation callback returns after consuming only a prefix or subset of its captured process steps
- **THEN** the completion ledger check fails that run and identifies the omitted step
- **AND** the contract does not claim that process or action effects completed earlier in that run never started
