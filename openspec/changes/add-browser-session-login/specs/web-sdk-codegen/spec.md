## ADDED Requirements

### Requirement: Open as uses generated panel client contracts

The committed generated TypeScript client SHALL include the canonical user-query and login-ticket operations, models, browser-login error enum values, and nullable pagination fields. React SHALL use those generated operations and SHALL keep the cross-origin consume form submission as a narrow browser transport rather than adding it to the JSON API client.

#### Scenario: Generated artifacts are current

- **WHEN** `make web-codegen-check` runs after browser-login HTTP models change
- **THEN** it succeeds only when the committed client matches the deterministic OpenAPI output

#### Scenario: Consume remains form navigation

- **WHEN** React receives a ticket response
- **THEN** it submits the declared action URL and ticket as a transient HTML form
- **AND** no generated JSON fetch method attempts to read the cross-origin redirect response
