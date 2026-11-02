## MODIFIED Requirements

### Requirement: Odoo and panel proxy contract

Every Odoo route SHALL preserve the validated external HTTPS Host, set trusted `X-Forwarded-Proto`, `X-Forwarded-Host`, and client-address headers, and proxy ordinary pages, redirects, static assets, downloads/uploads, and the Odoo 19 bus/WebSocket endpoint to the captured local runtime. It SHALL enforce configured Basic Auth when `odoo_basic_auth_enabled` is true and omit that layer when false; this setting SHALL NOT change Odoo's own authorization. When the owned route set contains a live panel route, each instance route SHALL add a mutually exclusive exact `POST /_odcli/auth/consume` branch to that existing panel `local_endpoint` before the general Odoo proxy. No other dashboard API/static route or consume method SHALL reach the panel backend through an instance host. The panel route SHALL always protect SPA and API with configured Basic Auth at the same HTTPS origin and proxy only to the captured local monitor endpoint. Caddy SHALL select the configured hash algorithm for Basic Auth. Unknown hosts SHALL match no OdCLI backend. In upstream HTTP mode, the external proxy SHALL terminate HTTPS and forward the original Host to Caddy's configured HTTP listener; only configured trusted proxy peers SHALL reach the HTTP routes, and other peers SHALL receive 403.

#### Scenario: Published Odoo browser flow

- **WHEN** an authenticated browser uses ordinary Odoo pages or the exact Open as consume POST through a published URL while a live panel route exists
- **THEN** ordinary requests reach the selected runtime and only exact consume POST reaches the existing panel endpoint with the authoritative external scheme and host

#### Scenario: Panel route is absent

- **WHEN** an instance route is rendered without a live panel route in the same owned route set
- **THEN** no consume branch is rendered and all existing Odoo publication behavior remains unchanged

#### Scenario: Odoo route without secondary Basic Auth

- **WHEN** `odoo_basic_auth_enabled` is false and a browser requests a published Odoo URL or exact consume path without Basic Auth
- **THEN** Caddy applies no secondary Odoo-route Basic Auth while the panel route remains protected

#### Scenario: Unknown or unauthorized path

- **WHEN** a request uses an unregistered Host, omits required Basic Auth, requests another dashboard path, or uses a non-POST consume method
- **THEN** Caddy forwards it to no unauthorized OdCLI management backend
