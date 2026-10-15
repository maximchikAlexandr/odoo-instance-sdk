## MODIFIED Requirements

### Requirement: Canonical CLI traceability

`PublicLeafCase`/`PUBLIC_LEAF_CASES` in `tests/unit/test_cli_output_modes.py` SHALL be the sole exhaustive CLI leaf inventory and SHALL remain test-only. Each row SHALL carry one classification, SDK primitive or concrete CLI-only reason, dry-run metadata, exactly one E2E disposition of `critical`, `focused`, `smoke`, or `not-applicable`, and a non-empty rationale. Collection-time assertions SHALL reject missing or extra rows for registered public leaves. Production code SHALL NOT import this matrix or maintain a runtime operation registry, stable operation IDs, provider bindings, or a second command table. The published command matrix SHALL be generated from the test-owned inventory.

#### Scenario: New CLI leaf cannot escape classification

- **WHEN** a public leaf is added to Click registration
- **THEN** test collection SHALL fail until the test-owned inventory adds exactly one complete classified row with an E2E disposition and non-empty rationale

#### Scenario: Production remains independent of test inventory

- **WHEN** production imports and registered CLI commands are inspected
- **THEN** production contains no import of the test-owned matrix and no replacement operation registry or binding table

#### Scenario: Matrix is generated from the test inventory

- **WHEN** the traceability document is checked
- **THEN** its leaf paths, classes, dry-run requirements, E2E dispositions, and evidence identifiers SHALL equal the generated projection of the test-owned inventory
