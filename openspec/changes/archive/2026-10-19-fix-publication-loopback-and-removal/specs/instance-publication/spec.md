## MODIFIED Requirements

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
