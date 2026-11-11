## ADDED Requirements

### Requirement: Open as uses the generated panel client

The committed generated TypeScript client SHALL include the canonical bounded user-query and handoff-issuance operations, models, and errors. React SHALL use those generated operations while keeping cross-origin consume as a transient HTML form rather than a JSON client method.

#### Scenario: Generated artifacts are current

- **WHEN** `make web-codegen-check` runs after browser-login HTTP models change
- **THEN** it succeeds only when the committed client matches deterministic OpenAPI output

#### Scenario: Consume remains form navigation

- **WHEN** React receives a successful handoff response
- **THEN** it submits the declared action URL and handoff through the transient form
- **AND** no generated fetch operation follows or reads the cross-origin redirect
