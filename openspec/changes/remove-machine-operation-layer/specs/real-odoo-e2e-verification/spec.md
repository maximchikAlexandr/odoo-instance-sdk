## MODIFIED Requirements

### Requirement: Canonical CLI traceability

`PublicLeafCase`/`PUBLIC_LEAF_CASES` in `tests/unit/test_cli_output_modes.py` SHALL remain the sole CLI leaf inventory and SHALL be test-only. Each row SHALL carry exactly one E2E disposition of `critical`, `focused`, `smoke`, or `not-applicable` with a non-empty rationale. Collection-time assertions SHALL require exactly one row and disposition for every registered public leaf. Production code SHALL NOT import this matrix or maintain a runtime operation registry, stable operation IDs, contract schemas, provider bindings, or a second command table. E2E parameterization and the published matrix SHALL be generated from the test-owned inventory.

#### Scenario: New CLI leaf cannot escape classification

- **WHEN** a public leaf is added to Click registration
- **THEN** test collection SHALL fail until the test-owned inventory adds exactly one classified row with an E2E disposition and non-empty rationale

#### Scenario: Production remains independent of test inventory

- **WHEN** production imports and registered CLI commands are inspected
- **THEN** production contains no import of the test-owned matrix and no replacement operation registry or binding table

#### Scenario: Matrix is generated from the test inventory

- **WHEN** the traceability document is checked
- **THEN** its leaf paths, classes, dry-run requirements, E2E dispositions, and evidence identifiers SHALL equal the generated projection of the test-owned inventory

## ADDED Requirements

### Requirement: Scope-changing revisions require fresh acceptance evidence

When an accepted delivery changes its product scope, the new graph revision SHALL identify one exact candidate SHA and SHALL obtain new applicable focused tests, required CI, independent review, and selected live E2E evidence for that composition. Results from an earlier graph revision MAY inform case selection but SHALL NOT satisfy any acceptance gate for the new revision.

#### Scenario: Machine layer is removed after graph-1 verification

- **WHEN** graph revision 2 removes operation registry, invoke/session, provider, or consumer surfaces from a graph-1 candidate
- **THEN** graph-1 WP completion, review, CI, and E2E results remain historical and graph 2 stays unaccepted until its own exact-SHA gates pass

#### Scenario: Revision evidence identifies its candidate

- **WHEN** a graph-2 review, CI, or E2E result is published
- **THEN** it records graph revision 2 and the exact tested product SHA and rejects a mismatch before claiming success
