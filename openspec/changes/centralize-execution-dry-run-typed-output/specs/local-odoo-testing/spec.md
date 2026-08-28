## ADDED Requirements

### Requirement: Odoo test command parity

The public Odoo test operation SHALL expose a command object whose plan contains all resolved selection/provenance observations, the exact Odoo shell argv, and the exact redacted test-runner stdin. CLI `test --dry-run` and normal execution SHALL use that same object without repeating addon, Git, installed-module, port, or database planning.

#### Scenario: Changed-test dry-run

- **WHEN** `odcli test --changed --dry-run` resolves a valid test selection
- **THEN** Git probes appear as read-only planning observations
- **AND** the Odoo test process does not start
- **AND** normal `.run()` later consumes the captured selection and process step or fails stale

#### Scenario: Test precondition changes

- **WHEN** selected Git files, installed modules, database identity, or reserved port differs before execution
- **THEN** execution fails closed before the Odoo test process starts
- **AND** it does not recompute a different test selection
