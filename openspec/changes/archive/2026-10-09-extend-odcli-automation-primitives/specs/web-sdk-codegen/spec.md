## ADDED Requirements

### Requirement: Generated client carries canonical publication fields
Deterministic OpenAPI export and the exact-pinned TypeScript generator SHALL include the publication model on project and environment snapshots. React SHALL import those generated types and SHALL NOT duplicate publication enums, external URL construction, or unavailability logic in handwritten models.

#### Scenario: Publication schema changes
- **WHEN** the Python publication model changes
- **THEN** the stale-output gate fails until OpenAPI and generated TypeScript are regenerated deterministically

### Requirement: Odoo-like panel uses external publication state
The panel SHALL render compact Odoo-style navigation, tables/lists, buttons, fields, typography, spacing, and status indicators for both project and environment runtimes. `Open Odoo` SHALL open only the server-supplied external HTTPS URL when publication is `available`; otherwise it SHALL be disabled and show the server-supplied reason. The local endpoint SHALL remain visible as operational data but SHALL NOT be opened from an external browser. API calls and assets SHALL use relative URLs under the current origin.

#### Scenario: Open a published main checkout
- **WHEN** a project runtime publication is `available`
- **THEN** its project row exposes an enabled `Open Odoo` action that opens the typed external URL with `noopener,noreferrer`

#### Scenario: Publication is unavailable
- **WHEN** a project or environment is unpublished, stopped, stale, or publication collection failed
- **THEN** `Open Odoo` is disabled and the row presents the typed reason without constructing a fallback localhost or domain URL

#### Scenario: Panel is served below the external origin
- **WHEN** the SPA is loaded through its Caddy HTTPS host
- **THEN** generated API calls, assets, polling, CSRF, and navigation remain relative to that origin
