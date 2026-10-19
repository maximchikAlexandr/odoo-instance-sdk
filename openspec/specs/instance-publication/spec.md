# instance-publication Specification

## Purpose
Define stable project/environment publication through a validated OdCLI-owned Caddy route file, a protected panel route, lifecycle reconciliation and the boundary between local route application and external acceptance.
## Requirements
### Requirement: Stable publication identity and settings
OdCLI SHALL store publication settings in the canonical owner-only user configuration root. Settings SHALL contain a normalized domain suffix, dedicated OdCLI-owned Caddy route-file path, local Caddy control endpoint, Basic Auth username and password hash for the panel, and panel host label; they SHALL reject schemes, ports, paths, credentials, wildcard characters, malformed DNS labels, plaintext passwords, and owned-route paths outside the owned root. An optional existing Caddy reload configuration and paired upstream HTTP listen port/bind IP SHALL support deployments where an upstream proxy terminates HTTPS; the upstream bind IP SHALL be a specific non-wildcard local address, and trusted proxy peers SHALL be explicit IP addresses. IPv4 and IPv6 loopback peers, including `127.0.0.1` and `::1`, SHALL be accepted. Unspecified, multicast and reserved non-loopback peers SHALL be rejected before route mutation. The `odoo_basic_auth_enabled` setting SHALL default to true and SHALL affect only Odoo routes. A project URL SHALL use a deterministic `project-<stable-project-id>` label and an environment URL SHALL use `env-<stable-environment-id>` so restarts preserve addresses and distinct owners cannot collide.

#### Scenario: Two runtimes receive stable distinct URLs
- **WHEN** a project runtime and an environment runtime are published under the same configured suffix
- **THEN** each receives a different HTTPS URL derived from its persisted owner identity
- **AND** republishing either owner returns the same URL

#### Scenario: Invalid publication settings
- **WHEN** the suffix, Caddy endpoint, owned path, username, or password hash is absent or invalid
- **THEN** publication fails before probing the runtime or changing Caddy configuration

#### Scenario: Incomplete upstream HTTP configuration
- **WHEN** the upstream port or bind address is supplied without its pair or without an existing reload configuration
- **THEN** settings validation fails before any route mutation

#### Scenario: Explicit loopback trusted peers
- **WHEN** publication settings specify `127.0.0.1` or `::1` as a trusted proxy peer
- **THEN** settings validation accepts that exact peer without broadening the trusted network

#### Scenario: Unsafe trusted peers remain rejected
- **WHEN** a trusted proxy peer is unspecified, multicast or reserved without being loopback
- **THEN** settings validation rejects it before route mutation

### Requirement: Typed publish and unpublish operations
The public SDK SHALL expose inspectable `publish_command()` and `unpublish_command()` operations and delegating convenience methods for exactly one project or environment runtime. The CLI SHALL expose `odcli publish` and `odcli unpublish` with mutually exclusive `--project PATH` and `--env ENV` selectors. Publish SHALL resolve the persisted runtime identity and local endpoint, require Odoo readiness, apply the owner route in the single owned route file, and return owner identity, local endpoint, external URL, and status. Unpublish SHALL remove only that owner's route, be idempotent when already absent, and leave runtime data unchanged.

#### Scenario: Publish a ready project runtime
- **WHEN** `odcli publish --project PATH` selects the captured ready project runtime and Caddy accepts the candidate configuration
- **THEN** the project route becomes active and the typed result returns its local endpoint and stable external HTTPS URL

#### Scenario: Runtime is not ready
- **WHEN** the selected runtime is stopped, stale, not ready, foreign, or its health probe fails
- **THEN** publish fails without changing the last working Caddy configuration or prior publication record

#### Scenario: Remove one route
- **WHEN** `odcli unpublish --env ENV` selects a published environment
- **THEN** only that environment's route is removed and a repeated call succeeds as already absent

### Requirement: Validated OdCLI-owned Caddy configuration
Publication mutations SHALL hold one canonical publication lock, read the current owned route file, produce a complete deterministic candidate for only that file, validate it with the configured Caddy executable, and reload through the shared process boundary. Reload SHALL use the owned route file by default or the configured existing Caddy root file that imports it. The configured HTTP Caddy control endpoint SHALL be converted to its network authority (host and port) for `caddy reload --address`; the full HTTP URL SHALL NOT be passed as that argument. The owned file SHALL be atomically replaced only as part of the validated reload operation; reload failure SHALL restore its prior bytes while Caddy retains its prior live configuration. OdCLI SHALL NOT create a publication database, edit the existing root/ingress configuration itself, or expose Caddy's administrative endpoint externally.

#### Scenario: Candidate configuration is rejected
- **WHEN** candidate validation or reload fails
- **THEN** the prior owned file remains byte-for-byte authoritative, prior live routes continue to work, and the result reports a bounded sanitized failure
- **AND** a failed recovery reload reports that live rollback was not confirmed

#### Scenario: Concurrent publications
- **WHEN** two owners are published concurrently
- **THEN** the lock serializes candidate generation and the second candidate includes the first successful route instead of losing it

#### Scenario: Existing Caddy root imports OdCLI routes
- **WHEN** upstream HTTP mode selects an existing Caddy root configuration and an OdCLI-owned route file imported by that root
- **THEN** the owned candidate contains host-specific HTTP listener and bind directives, and reload uses the existing root configuration without replacing its unrelated routes
- **AND** publication fails before mutation unless that root imports the exact owned route file

### Requirement: Odoo and panel proxy contract
Every Odoo route SHALL preserve the validated external HTTPS Host, set trusted `X-Forwarded-Proto`, `X-Forwarded-Host`, and client-address headers, and proxy ordinary pages, redirects, static assets, downloads/uploads, and the Odoo 19 bus/WebSocket endpoint to the captured local runtime. It SHALL enforce configured Basic Auth when `odoo_basic_auth_enabled` is true and omit that layer when false; this setting SHALL NOT change Odoo's own authorization. The panel route SHALL always protect SPA and API with configured Basic Auth at the same HTTPS origin and proxy only to the captured local monitor endpoint. Caddy SHALL select the configured hash algorithm for Basic Auth. Unknown hosts SHALL match no OdCLI backend. In upstream HTTP mode, the external proxy SHALL terminate HTTPS and forward the original Host to Caddy's configured HTTP listener; only configured trusted proxy peers SHALL reach the HTTP routes, and other peers SHALL receive 403.

#### Scenario: Published Odoo browser flow
- **WHEN** an authenticated browser uses login, assets, redirects, attachments, and bus/WebSocket through a published URL
- **THEN** each request reaches the selected runtime with the correct external scheme and host semantics

#### Scenario: Odoo route without secondary Basic Auth
- **WHEN** `odoo_basic_auth_enabled` is false and a browser requests a published Odoo URL without Basic Auth
- **THEN** Caddy forwards the request to Odoo while the panel route still rejects unauthenticated requests

#### Scenario: Unknown or unauthenticated panel host
- **WHEN** a request uses an unregistered Host or omits valid Basic Auth for the panel
- **THEN** Caddy forwards it to no OdCLI backend

### Requirement: Publication lifecycle reconciliation
Stopping a runtime SHALL leave its deterministic route entry in place but report publication as `backend_unavailable` with a reason and no actionable external link. Restart plus republish SHALL reactivate the same address. Environment removal SHALL first pass its existing ownership, runtime, port and dirty-worktree preflight, then delete its route before final environment removal and SHALL enter `cleanup_failed` if route cleanup cannot be proven; explicit unpublish SHALL be available for retry. Route cleanup SHALL apply regardless of whether an SDK-owned worktree exists; cleanup failure SHALL preserve the remaining owned worktree and configuration. Without publication settings, removal SHALL retain its local-only behavior. Project removal SHALL NOT be inferred from a missing directory.

#### Scenario: Published runtime stops and restarts
- **WHEN** a published runtime stops and is later restarted and republished
- **THEN** monitor state changes from available to `backend_unavailable` and back while the external URL identity remains unchanged

#### Scenario: Environment deletion cannot remove its route
- **WHEN** environment removal cannot validate and reload a candidate without that environment route
- **THEN** worktree/catalog cleanup does not claim success and the retryable publication cleanup failure is recorded

#### Scenario: Existing SDK-owned worktree is removed
- **WHEN** a published environment passes removal preflight and its SDK-owned worktree exists
- **THEN** its route is removed before worktree and configuration deletion

#### Scenario: Missing SDK-owned worktree is cleaned up
- **WHEN** a published environment passes removal preflight and its worktree is already absent
- **THEN** its route is still cleaned before the remaining owned artifacts are removed

#### Scenario: Removal preflight is blocked
- **WHEN** an SDK-owned worktree is dirty or its reserved shared-environment port is occupied
- **THEN** removal fails without attempting publication cleanup or deleting owned artifacts

#### Scenario: Local-only removal
- **WHEN** no publication settings are configured and the environment passes removal preflight
- **THEN** removal completes without invoking publication cleanup

### Requirement: Publication result describes local route application

A successful published/already_published result SHALL describe the validated local runtime and applied OdCLI-owned Caddy route file; it SHALL NOT claim external DNS, TLS, tunnel reachability or an authenticated browser session were verified. The owned file is a complete standalone Caddy configuration by default. When an existing root configuration is explicitly selected, the owned file is imported into it and reload uses that root; OdCLI does not edit the root or claim to own the upstream proxy.

#### Scenario: Caddy reload uses a network address

- **WHEN** settings supply a local HTTP control endpoint and publication reload runs
- **THEN** its argv uses the endpoint host:port authority for --address and validation/reload failure retains the existing recovery contract

#### Scenario: Local route is applied before external acceptance

- **WHEN** a healthy runtime route is validated and successfully reloaded
- **THEN** the result can be published without claiming an external HTTPS/login probe; external acceptance is a separate verification step
