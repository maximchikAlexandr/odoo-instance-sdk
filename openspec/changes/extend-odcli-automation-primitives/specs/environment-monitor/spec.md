## ADDED Requirements

### Requirement: Runtime snapshots expose publication separately from local endpoints
`ProjectSummary` and `EnvironmentSnapshot` SHALL expose a typed publication projection containing state `unpublished`, `available`, `backend_unavailable`, or `error`, stable external URL when configured, sanitized unavailability reason, and sampled route identity. Existing `RuntimeMetrics.http_url` SHALL remain the local endpoint and SHALL NOT be overwritten by the external URL. Collection SHALL read the OdCLI-owned route file and correlate its owner/route identity with the same captured runtime identity used by status/stop; it SHALL remain read-only and SHALL NOT introduce a publication database.

#### Scenario: Published environment is ready
- **WHEN** an environment has a matching active route and its exact runtime is ready
- **THEN** the snapshot reports local `http_url`, distinct external HTTPS URL, and publication `available`

#### Scenario: Route exists but runtime stopped
- **WHEN** a persisted publication route exists and the exact runtime is stopped or stale
- **THEN** the stable external URL remains identifiable, publication is `backend_unavailable`, and the reason explains why navigation is disabled

#### Scenario: Publication data cannot be read
- **WHEN** publication persistence or route inspection fails
- **THEN** runtime/cluster/environment facts remain available and only publication is marked `error`

### Requirement: Main checkout and environment publication use one model
The canonical snapshot SHALL expose the same publication type for a project-owned runtime and every environment runtime. CLI inventory, HTTP API, generated TypeScript, and process inventory SHALL project the canonical fields rather than recollect Caddy state or construct external URLs independently.

#### Scenario: Project-only snapshot
- **WHEN** a project has a published main-checkout runtime and no environments
- **THEN** the project summary contains its publication and the dashboard can render and open it without a synthetic environment row
