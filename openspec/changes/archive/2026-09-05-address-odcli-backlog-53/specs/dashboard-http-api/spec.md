## ADDED Requirements

### Requirement: Project-owned runtimes use the canonical dashboard snapshot

`GET /api/v1/snapshot` SHALL serialize project-owned runtime data through the same public typed `Snapshot` model and msgspec/OpenAPI bridge as environment-owned data. The JavaScript client and dashboard SHALL render a project runtime even when the project's environments array is empty, including readiness, URL, database, PID/process count, CPU, RAM, and applicable cluster metrics. No parallel endpoint or frontend-only response shape SHALL be introduced.

#### Scenario: API serializes project-only state
- **WHEN** the monitor returns an initialized project with a live project-owned runtime and no environments
- **THEN** the API response validates against the published Snapshot schema and exposes that runtime without a synthetic environment

#### Scenario: Dashboard renders project runtime
- **WHEN** the generated client receives the project-only snapshot
- **THEN** the dashboard shows the running instance and its metrics instead of the no-environments empty state
