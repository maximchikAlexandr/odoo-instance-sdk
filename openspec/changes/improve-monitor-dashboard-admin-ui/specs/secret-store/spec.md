## ADDED Requirements

### Requirement: Safe Admin projections carry opaque route identity

Each safe remote Odoo and GitLab secret projection SHALL expose a deterministic `admin_id` for Starlette Admin routing. The identifier SHALL be derived only from the record's normalized public identity fields and current positive revision, SHALL round-trip through one exact bounded decoder, and SHALL never include or derive from a password, token, ciphertext, encryption key, internal token key, or secret length. It is an Admin transport identity, not a persisted schema column or credential lookup key.

#### Scenario: Projection identity round-trips

- **WHEN** a safe secret projection is listed and its `admin_id` is decoded by the matching Admin view
- **THEN** the original normalized identity and current revision are recovered exactly without reading secret material

#### Scenario: Admin identity is not persisted

- **WHEN** the encrypted store schema and committed record are inspected
- **THEN** no new `admin_id` column or duplicate identity authority exists

#### Scenario: Safe representation remains secret-free

- **WHEN** list, detail, form-load, validation, response, log, exception, repr, OpenAPI, or generated-client output contains a safe secret projection
- **THEN** `admin_id` reveals no stored secret or secret-derived metadata and the existing write-only-field contract remains intact
