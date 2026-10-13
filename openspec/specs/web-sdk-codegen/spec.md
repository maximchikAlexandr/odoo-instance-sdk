# web-sdk-codegen Specification

## Purpose
Define deterministic OpenAPI/TypeScript SDK generation and the dashboard presentation of project and worktree runtimes through those canonical API contracts.
## Requirements
### Requirement: Canonical deterministic OpenAPI export

The repository MUST store canonical `openapi.json` exported directly from `create_app(headless=False, static_assets=False, monitor=<schema stub>, pgadmin_opener=<typed no-op>, runtime_controller=<typed no-op>).openapi()` without starting an HTTP server. `static_assets=False` MUST preserve the UI API route set while skipping the packaged-SPA existence check and mount; normal runtime UI composition MUST keep static assets enabled by default. Export MUST use sorted object keys and deterministic formatting and MUST contain no timestamp, absolute path, environment value, random identifier, local port allocation, secret, or machine-specific server URL. Two consecutive exports from identical sources MUST be byte-identical.

The export path MUST work in a clean checkout with ignored `web/dist` absent and MUST perform no source-tree asset access, public resource construction, Docker inspection/invocation, credential/configuration creation, socket bind, server start, or browser open. The injected schema monitor/opener/runtime controller are inert typed test/export dependencies only.

#### Scenario: Consecutive exports are identical

- **WHEN** OpenAPI export runs twice without source changes
- **THEN** both `openapi.json` byte sequences are identical

#### Scenario: Export is serverless and side-effect free

- **WHEN** OpenAPI is exported on a machine with no running dashboard or pgAdmin container
- **THEN** export succeeds without binding a port, opening a browser, creating secrets, or invoking Docker

#### Scenario: Clean checkout needs no frontend build for export

- **WHEN** Python dashboard/test dependencies and Node dependencies are installed but `web/dist` does not exist
- **THEN** canonical export and `make web-codegen-check` succeed before frontend tests or `npm run build`

### Requirement: Exact-pinned Fetch SDK generation

The frontend MUST exact-pin `@hey-api/openapi-ts` as a development dependency and generate only TypeScript models, a Fetch client, and flat tree-shakeable operations under `src/odoo_instance_sdk/web/src/generated/`. Its development dependency chain MUST resolve `js-yaml` to version `4.3.1` or newer. Every generated source file MUST carry an `@generated` header and MUST be reproducible from canonical `openapi.json`; generated output MUST be committed.

Hey API adoption MUST pass all of these gates: stable operation names `getMonitorSnapshot`, `openPgAdmin`, `startRuntime`, and `stopRuntime`, exact nullability/enums, no Axios or framework hooks, no generated mocks, no custom templates, flat tree-shakeable callable operations, the required generated header, and byte-identical consecutive output. If any gate fails, the only permitted fallback is exact-pinned `openapi-typescript` plus `openapi-fetch` with the same output contract; no additional generator evaluation is allowed.

#### Scenario: Generated operations are callable

- **WHEN** generation completes from canonical OpenAPI
- **THEN** React can import callable Fetch operations for `getMonitorSnapshot`, `openPgAdmin`, `startRuntime`, and `stopRuntime` plus their generated request/response/error models

#### Scenario: Generation is deterministic

- **WHEN** SDK generation runs twice from unchanged `openapi.json` and lockfile
- **THEN** all files under `web/src/generated/` are byte-identical

### Requirement: React consumes only generated API contracts

React MUST remove handwritten API interfaces/types and endpoint-specific `fetch()` wrappers from `web/src/api.ts`. The only handwritten runtime client module MUST configure the generated Fetch client's relative `baseUrl` and attach the readable CSRF cookie as `X-CSRF-Token` on POST requests; UI polling, errors, runtime actions, and any enabled pgAdmin actions MUST invoke generated operations and use generated types. React MUST NOT reimplement Python eligibility, ownership, cluster-health, nullability, or enum invariants.

#### Scenario: No handwritten API model copy remains

- **WHEN** frontend sources are inspected after generation
- **THEN** snapshot, runtime action, pgAdmin, and HTTP error types are imported from `web/src/generated/` and no equivalent handwritten interfaces or direct endpoint-specific `fetch()` call remains

#### Scenario: Environment card uses typed eligibility

- **WHEN** the pgAdmin dashboard action is enabled after the credential flow in GitHub #147 is completed
- **THEN** its pgAdmin button enabled/disabled state and explanation come from the generated `pgadmin.state` field, and clicking an eligible button invokes generated `openPgAdmin`

#### Scenario: pgAdmin dashboard action is deferred

- **WHEN** an environment card renders while GitHub #147 remains deferred
- **THEN** the dashboard renders no pgAdmin action, eligibility status, or error panel and makes no pgAdmin open request; the generated API and backend operation remain available

### Requirement: Codegen commands and stale-output gate

The root Makefile MUST provide `make web-codegen` to export canonical OpenAPI and regenerate the SDK, and `make web-codegen-check` to run generation in a clean comparison flow and fail on any stale or nondeterministic `openapi.json` or generated file. The check MUST preserve the developer's working files. A clean-checkout CI job MUST run in this exact dependency/order boundary: install Python dashboard/test dependencies, run `npm ci`, run `make web-codegen-check` while `web/dist` is absent, run frontend tests, then `npm run build`; backend contract/package checks MAY follow but MUST NOT be a prerequisite that creates `dist` for codegen.

#### Scenario: Stale generated SDK fails verification

- **WHEN** a Python HTTP model or operation changes without regenerating committed output
- **THEN** `make web-codegen-check` exits non-zero and identifies generated artifacts as stale

#### Scenario: Clean generated SDK passes verification

- **WHEN** committed OpenAPI and generated files match two consecutive generation runs
- **THEN** `make web-codegen-check`, frontend tests, and frontend build exit zero

### Requirement: Generated client carries canonical publication fields
Deterministic OpenAPI export and the exact-pinned TypeScript generator SHALL include the publication model on project and environment snapshots. React SHALL import those generated types and SHALL NOT duplicate publication enums, external URL construction, or unavailability logic in handwritten models.

#### Scenario: Publication schema changes
- **WHEN** the Python publication model changes
- **THEN** the stale-output gate fails until OpenAPI and generated TypeScript are regenerated deterministically

### Requirement: Odoo-like panel uses external publication state
The panel SHALL render compact Odoo 19-style navigation, a project selector, equal-size runtime cards, buttons, fields, typography, spacing, and status indicators for both project and environment runtimes. `Open Odoo` SHALL open only the server-supplied external HTTPS URL when publication is `available`; otherwise it SHALL be disabled and expose the server-supplied reason through the action tooltip, with a publish instruction when no reason is supplied. The local endpoint SHALL remain visible as operational data but SHALL NOT be opened from an external browser. API calls and assets SHALL use relative URLs under the current origin.

#### Scenario: Open a published main checkout
- **WHEN** a project runtime publication is `available`
- **THEN** its project card exposes an enabled `Open Odoo` action that opens the typed external URL with `noopener,noreferrer`

#### Scenario: Publication is unavailable
- **WHEN** a project or environment is unpublished, stopped, stale, or publication collection failed
- **THEN** `Open Odoo` is disabled and the action tooltip presents the typed reason without constructing a fallback localhost or domain URL

#### Scenario: Panel is served below the external origin
- **WHEN** the SPA is loaded through its Caddy HTTPS host
- **THEN** generated API calls, assets, polling, CSRF, and navigation remain relative to that origin

### Requirement: Dashboard runtime controls

As defined in dashboard-http-api, the non-headless monitor SHALL expose typed `POST /api/v1/runtime/start` and `POST /api/v1/runtime/stop` operations with `RuntimeActionRequest` (`owner_kind`, `owner_id`) and `RuntimeActionResult`. Requests SHALL resolve registered project or environment identities and delegate to public SDK detached-launch and runtime-stop commands, retaining SDK preflight, locks and process ownership checks. Caller-supplied paths, argv and environment overrides SHALL NOT be accepted. Browser Origin, JSON content type and CSRF checks SHALL run before delegation. Headless monitors SHALL omit these operations. Externally proxied monitors SHALL accept mutations only behind the configured trusted proxy, exact HTTPS host and same-origin CSRF boundary; the proxy SHALL authenticate the entire panel and API.

#### Scenario: Start and Stop follow runtime state

- **WHEN** an instance is running (ready or not ready)
- **THEN** its card offers Stop, and after a successful stop offers Start; a stopped or unrecorded runtime offers Start and switches to Stop after launch; a non-ready environment lifecycle disables the control
- **AND** controls show Starting/Stopping in the action and status and reject repeated clicks while a request is pending; SDK rejection is shown on the affected card without exposing private diagnostic details

#### Scenario: Generated runtime client

- **WHEN** the frontend invokes a runtime action
- **THEN** it uses generated `startRuntime` or `stopRuntime`, sends the catalog owner identity and refreshes the snapshot; no handwritten endpoint contract is added and no publication or database-preparation operation is implicitly performed


### Requirement: Shared compact runtime card presentation

Main checkouts and worktrees SHALL use the same card component and responsive grid, with equal card width and height at the same breakpoint. The panel SHALL load Mantine core styles and an Odoo 19-inspired theme with plum navigation/actions, compact field styling and a project selector. It SHALL retain all-project and selected-project views, show registered main checkouts without environment rows, and preserve snapshot polling, retry and request coalescing.

Each card SHALL show instance name, Main checkout or Worktree kind, one status indicator, a Branch row, database and local endpoint. The main Branch value SHALL use `ProjectSummary.runtime.branch`, which records the launch branch and may be unavailable after stop; the worktree SHALL use `EnvironmentSnapshot.branch`. Missing branch data SHALL show `Not recorded` without guessing from the instance name. CPU and RAM SHALL be shown only for live runtimes with non-null values. Non-ready environment lifecycle state SHALL take precedence over runtime readiness in the single status indicator.

The compact panel SHALL hide cluster/container diagnostics and badges, separate port/publication-state text, PID/worker IDs, Git/SHA/diff details, storage/disk/worktree/venv breakdowns and pgAdmin controls/status. These presentation changes SHALL NOT remove their backend snapshot fields or change metric collection. pgAdmin restoration remains deferred to #147 with a TODO at the action area.

#### Scenario: Main checkout and worktree share geometry

- **WHEN** a selected project has one main runtime and one worktree
- **THEN** both render equal-size cards with the same field/action layout and exactly one status indicator

#### Scenario: Branch data is available or absent

- **WHEN** the generated snapshot supplies a launch branch for the main checkout and a catalog branch for a worktree
- **THEN** each card displays its corresponding value under Branch; absent main runtime/branch displays Not recorded

#### Scenario: Metrics and technical details are hidden selectively

- **WHEN** a runtime has unavailable CPU/RAM or its project cluster reports unavailable metrics
- **THEN** the card shows no empty metric placeholders or cluster diagnostics while preserving available runtime values and backend fields

#### Scenario: No hand-written Basic Auth credentials

- **WHEN** the panel and API share one HTTPS origin protected by Caddy Basic Auth
- **THEN** generated Fetch operations use the browser's same-origin credentials behavior; React does not embed the proxy login/password or manually construct its Authorization header
