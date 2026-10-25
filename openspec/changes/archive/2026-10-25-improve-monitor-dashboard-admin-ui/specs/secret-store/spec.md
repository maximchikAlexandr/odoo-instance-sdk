## ADDED Requirements

### Requirement: Safe Admin projections carry opaque route identity

Each safe remote Odoo and GitLab projection SHALL expose a deterministic `admin_id` derived only from normalized public identity and current positive revision. It SHALL round-trip through one exact bounded decoder and SHALL contain or derive from no password, token, ciphertext, key material, internal token key, or secret length. It SHALL remain an Admin transport identity, not a persisted column or credential lookup key.

#### Scenario: Projection identity round-trips

- **WHEN** a safe secret projection is listed and its `admin_id` is decoded by the matching Admin view
- **THEN** the original normalized identity and current revision are recovered exactly without reading secret material

#### Scenario: Admin identity is not persisted

- **WHEN** the encrypted store schema and committed record are inspected
- **THEN** no new `admin_id` column or duplicate identity authority exists

#### Scenario: Safe representation remains secret-free

- **WHEN** list, detail, form-load, validation, response, log, exception, repr, OpenAPI, or generated-client output contains a safe secret projection
- **THEN** `admin_id` reveals no stored secret or secret-derived metadata and the existing write-only-field contract remains intact
