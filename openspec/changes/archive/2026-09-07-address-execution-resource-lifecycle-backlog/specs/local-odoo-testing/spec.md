## ADDED Requirements

### Requirement: Odoo test progress and interruption

Top-level `test` and compatibility `module test` SHALL use the same observed bounded command and SHALL expose current logical step, completed steps, and elapsed time during normal Rich execution. A controlled interruption SHALL close the renderer and owned process tree, preserve existing test diagnostics, and return exit 130. Machine formats and dry-run SHALL remain progress-free.

#### Scenario: Test status precedes completion

- **WHEN** a controlled Odoo test process emits no result for a measurable interval
- **THEN** Rich output displays its current test step before the process completes

#### Scenario: Test command fails

- **WHEN** the observed Odoo test step exits non-zero
- **THEN** the step is reported failed and the existing test failure result and exit semantics are preserved

#### Scenario: Test is interrupted

- **WHEN** Ctrl-C interrupts `test` or `module test`
- **THEN** the renderer closes, the owned process group is cleaned up, and the command exits 130
