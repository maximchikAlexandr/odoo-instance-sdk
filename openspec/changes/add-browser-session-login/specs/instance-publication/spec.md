## MODIFIED Requirements

### Requirement: Odoo and panel proxy contract

Every Odoo route SHALL preserve the validated external HTTPS Host, set trusted `X-Forwarded-Proto`, `X-Forwarded-Host`, and client-address headers, and proxy ordinary pages, redirects, static assets, downloads/uploads, and the Odoo 19 bus/WebSocket endpoint to the captured local runtime. It SHALL enforce configured Basic Auth when `odoo_basic_auth_enabled` is true and omit that layer when false; this setting SHALL NOT change Odoo's own authorization. Before the general Odoo proxy, a mutually exclusive exact `POST /_odcli/auth/consume` branch SHALL proxy only that request to the captured live dashboard endpoint while preserving the same Basic Auth policy and authoritative host/proto boundary. No other dashboard API or static route SHALL be reachable on an instance host, and no other method at the consume path SHALL create a session. The panel route SHALL always protect SPA and API with configured Basic Auth at the same HTTPS origin and proxy only to the captured local monitor endpoint. Caddy SHALL select the configured hash algorithm for Basic Auth. Unknown hosts SHALL match no OdCLI backend. In upstream HTTP mode, the external proxy SHALL terminate HTTPS and forward the original Host to Caddy's configured HTTP listener; only configured trusted proxy peers SHALL reach the HTTP routes, and other peers SHALL receive 403.

#### Scenario: Published Odoo browser flow

- **WHEN** an authenticated browser uses login, assets, redirects, attachments, bus/WebSocket, or an Open as consume POST through a published URL
- **THEN** ordinary Odoo requests reach the selected runtime and only the exact consume POST reaches the dashboard backend with correct external scheme and host semantics

#### Scenario: Odoo route without secondary Basic Auth

- **WHEN** `odoo_basic_auth_enabled` is false and a browser requests a published Odoo URL or exact consume path without Basic Auth
- **THEN** Caddy applies no secondary Odoo-route Basic Auth while the panel route remains protected

#### Scenario: Unknown or unauthenticated panel host

- **WHEN** a request uses an unregistered Host or omits required Basic Auth for the instance or panel route
- **THEN** Caddy forwards it to no unauthorized OdCLI backend

#### Scenario: Instance route cannot expose the dashboard

- **WHEN** a client uses an instance host with another dashboard path or a non-POST consume method
- **THEN** Caddy does not route that request to a dashboard API capable of management actions or session creation
