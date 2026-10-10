# instance-publication Specification

## Purpose
TBD - created by archiving change extend-odcli-automation-primitives. Update Purpose after archive.
## Requirements
### Requirement: Stable publication identity and settings
OdCLI SHALL store publication settings in the canonical owner-only user configuration root. Settings SHALL contain a normalized domain suffix, dedicated OdCLI-owned Caddy aggregate configuration path, local Caddy control endpoint, Basic Auth username and password hash, and panel host label; they SHALL reject schemes, ports, paths, credentials, wildcard characters, malformed DNS labels, plaintext passwords, and configuration paths outside the owned root. A project URL SHALL use a deterministic `project-<stable-project-id>` label and an environment URL SHALL use `env-<stable-environment-id>` so restarts preserve addresses and distinct owners cannot collide.

#### Scenario: Two runtimes receive stable distinct URLs
- **WHEN** a project runtime and an environment runtime are published under the same configured suffix
- **THEN** each receives a different HTTPS URL derived from its persisted owner identity
- **AND** republishing either owner returns the same URL

#### Scenario: Invalid publication settings
- **WHEN** the suffix, Caddy endpoint, owned path, username, or password hash is absent or invalid
- **THEN** publication fails before probing the runtime or changing Caddy configuration

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
Publication mutations SHALL hold one canonical publication lock, read the current owned route file, produce a complete deterministic candidate for only that file, validate it with the configured Caddy executable, and reload it through the shared process boundary. The canonical file SHALL be atomically replaced only as part of the validated reload operation; reload failure SHALL restore its prior bytes while Caddy retains its prior live configuration. OdCLI SHALL NOT create a publication database, edit unowned ingress configuration, or expose Caddy's administrative endpoint externally.

#### Scenario: Candidate configuration is rejected
- **WHEN** candidate validation or reload fails
- **THEN** the prior owned file remains byte-for-byte authoritative, prior live routes continue to work, and the result reports a bounded sanitized failure

#### Scenario: Concurrent publications
- **WHEN** two owners are published concurrently
- **THEN** the lock serializes candidate generation and the second candidate includes the first successful route instead of losing it

### Requirement: Odoo and panel proxy contract
Every Odoo route SHALL enforce HTTPS and configured Basic Auth, preserve the validated external Host, set trusted `X-Forwarded-Proto`, `X-Forwarded-Host`, and client-address headers, and proxy ordinary pages, redirects, static assets, downloads/uploads, and the Odoo 19 bus/WebSocket endpoint to the captured local runtime. The panel route SHALL protect SPA and API with the same HTTPS/Auth boundary and proxy only to the captured local monitor endpoint. Unknown hosts SHALL match no OdCLI backend.

#### Scenario: Published Odoo browser flow
- **WHEN** an authenticated browser uses login, assets, redirects, attachments, and bus/WebSocket through a published URL
- **THEN** each request reaches the selected runtime with the correct external scheme and host semantics

#### Scenario: Unknown or unauthenticated host
- **WHEN** a request omits valid Basic Auth or uses an unregistered Host
- **THEN** Caddy rejects it without forwarding to Odoo or the monitor

### Requirement: Publication lifecycle reconciliation
Stopping a runtime SHALL leave its deterministic route entry in place but report publication as `backend_unavailable` with a reason and no actionable external link. Restart plus republish SHALL reactivate the same address. Environment removal SHALL delete its route before final environment removal and SHALL enter `cleanup_failed` if route cleanup cannot be proven; explicit unpublish SHALL be available for retry. Project removal SHALL NOT be inferred from a missing directory.

#### Scenario: Published runtime stops and restarts
- **WHEN** a published runtime stops and is later restarted and republished
- **THEN** monitor state changes from available to `backend_unavailable` and back while the external URL identity remains unchanged

#### Scenario: Environment deletion cannot remove its route
- **WHEN** environment removal cannot validate and reload a candidate without that environment route
- **THEN** worktree/catalog cleanup does not claim success and the retryable publication cleanup failure is recorded
