## ADDED Requirements

### Requirement: Explicit trusted external proxy mode
`create_app()` and `run_server()` SHALL accept a typed external proxy configuration containing exact allowed external Hosts and exact trusted proxy addresses. Local mode SHALL retain loopback-only Host and bind behavior. External mode SHALL accept only direct connections from configured proxies, derive external scheme/host only from their forwarded headers, reject unknown Host, malformed forwarding chains, and direct untrusted clients, and keep the backend endpoint non-public.

#### Scenario: Request arrives through the trusted Caddy proxy
- **WHEN** the peer is trusted and forwarded host/proto match the configured panel HTTPS origin
- **THEN** the SPA and API request are accepted with the external origin reconstructed consistently

#### Scenario: Forwarded headers arrive directly
- **WHEN** an untrusted peer supplies an allowed Host or `X-Forwarded-*` values
- **THEN** the request is rejected and forwarded values are ignored

### Requirement: Same-origin mutation protection survives HTTPS proxying
The CSRF cookie SHALL be `Secure` in external HTTPS mode, retain strict same-site semantics, and be checked with the effective trusted external origin. Relative API routes SHALL remain same-origin. Basic Auth SHALL be enforced at Caddy before the application; application authorization SHALL NOT infer trust merely from a Basic Auth header. Local pgAdmin opening SHALL be hidden or disabled in external mode unless a separate safe external URL is explicitly published.

#### Scenario: External state-changing request
- **WHEN** an authenticated external browser submits a panel mutation with matching secure cookie, token, effective HTTPS origin, and same-origin fetch metadata
- **THEN** the request passes the existing mutation boundary

#### Scenario: External browser tries a loopback-only action
- **WHEN** pgAdmin has only a loopback URL in external panel mode
- **THEN** the API/UI report it unavailable and do not return a browser-openable localhost action

### Requirement: Publication fields are served from the canonical snapshot
The snapshot operation and OpenAPI schema SHALL include the canonical project/environment publication model without a second route lookup in the HTTP adapter. Unexpected publication values SHALL be rejected by typed serialization and monitor failures SHALL retain existing sanitized error responses.

#### Scenario: External URL reaches the generated client
- **WHEN** the monitor returns a published project or environment
- **THEN** OpenAPI and runtime JSON describe the same publication state, URL, and reason consumed by the TypeScript SDK
