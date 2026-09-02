## MODIFIED Requirements

### Requirement: Bounded Expression dependency

The project SHALL keep Expression under the repository's bounded runtime dependency policy and reproducible lock only while the measured payoff remains non-negative. Production imports SHALL remain limited to pure internal planning modules; package metadata paths, Click registration, public execution models, process effects, cleanup, and serializers SHALL not import Expression.

The GitHub #35 vertical slice SHALL append a reproducible post-slice row to `docs/adr/0002-bounded-expression-checkout-assessment.md`. The row and adjacent explanation SHALL identify the exact pre/post revisions and affected native-argument planning functions, count `ast.If`, `ast.IfExp`, and `ast.Match` nodes before and after, count Expression boundary adapters/unwraps introduced by the slice, and evaluate the same stop condition used by the preliminary checkout assessment. If introduced adapters/unwraps exceed planning branches removed, the implementation SHALL remove Expression and its lock entry while retaining concrete typed stage signatures; otherwise it SHALL record why bounded retention remains justified. A slice that introduces zero Expression boundary operations SHALL still record the mandatory measurement and SHALL not claim that the checkout result waived it.

#### Scenario: Checkout pipeline payoff is measured

- **WHEN** the first checkout planning slice is complete
- **THEN** the ADR records before/after planning branches and Expression adapter/unwrap count
- **AND** the result remains a preliminary checkout assessment only
- **AND** a positive checkout result does not waive the mandatory reassessment after the #35 vertical slice

#### Scenario: Issue #35 vertical slice is complete

- **WHEN** native run-argument planning, validation, command capture, and parity tests are complete
- **THEN** the ADR contains the reproducible post-#35 branch-versus-adapter/unwrap row and stop-condition outcome
- **AND** strict tests verify that the post-#35 row is no longer pending
- **AND** Expression is removed before broader adoption if adapters/unwraps introduced by the slice exceed planning branches removed

#### Scenario: Issue #35 does not use Expression

- **WHEN** the native-argument slice uses a small pure validator and introduces no Expression adapter or unwrap
- **THEN** the ADR still records a zero adapter/unwrap count, the measured branch delta, and the evaluated retention/removal outcome

#### Scenario: Metadata startup runs

- **WHEN** fresh interpreters import `odoo_instance_sdk`, run `odcli --help`, or run `odcli --version`
- **THEN** Expression and `odoo_instance_sdk.internal.proc` remain absent from `sys.modules`
