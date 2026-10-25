# dashboard-http-api Specification

## Purpose
Define the optional FastAPI dashboard adapter, canonical typed HTTP contracts, read-only snapshots, protected SDK runtime controls and trusted external HTTPS proxy boundary.
## Requirements
### Requirement: Modular dashboard HTTP adapter

The dashboard HTTP adapter MUST be split into `odoo_instance_sdk.http.app` for `create_app`, loopback Host policy, health/static composition and server boot, and `odoo_instance_sdk.http.monitor` for the monitor `APIRouter`, snapshot serialization, and pgAdmin/runtime route composition; `odoo_instance_sdk.http.runtime` resolves catalog owners for Start/Stop and delegates execution to public instance command snapshots. The existing `odcli monitor` boot contract and import path MUST remain compatible. Dashboard-only dependencies MUST remain optional and lazily imported; importing the core SDK or CLI help MUST NOT import FastAPI, Starlette, Uvicorn, React assets, or generated TypeScript.

The split MUST NOT introduce a generic HTTP service layer, dependency-injection container, router registry, or Click imports in route modules. Routes MUST delegate catalog, Git, Docker, database, pgAdmin orchestration and runtime launch/stop effects to the existing monitor/public resource boundary.

#### Scenario: Core-only import remains dependency-neutral

- **WHEN** the package is installed without the dashboard extra and `odoo_instance_sdk`, `odoo_instance_sdk.cli`, or CLI help is imported
- **THEN** the import succeeds without FastAPI, Starlette, or Uvicorn being installed

#### Scenario: Existing monitor boot path is preserved

- **WHEN** `odcli monitor` starts in UI or headless mode after the module split
- **THEN** it preserves the current loopback host validation, port selection, browser-open, health route, and SPA composition behavior

### Requirement: Stable typed snapshot operation

`GET /api/v1/snapshot` MUST publish operation ID `getMonitorSnapshot`, use the public `Snapshot` model as its named `200 application/json` schema, and publish a named `HttpError` response for status `500`. The optional `project_id: string | null` query parameter MUST retain the existing filter semantics. The response MUST be the direct `msgspec` JSON serialization of `Snapshot`, never a CLI JSON/TOON envelope.

`HttpError` MUST be a frozen, unknown-field-forbidden typed Python model with exact fields `code: HttpErrorCode` and `message: str`. `HttpErrorCode` MUST be a string enum covering `invalid_request`, `monitor_snapshot_failed`, `environment_not_found`, `pgadmin_not_eligible`, `database_not_found`, `pgadmin_unavailable`, `runtime_not_found`, `runtime_conflict`, and `runtime_unavailable`. Error messages MUST be bounded and user-actionable but MUST NOT expose secrets, absolute/internal paths, raw exception text, Docker payloads, command output, decoder details, or submitted invalid values. `openPgAdmin` MUST publish a named `HttpError` response for status `422`; malformed JSON, missing/non-string `environment_id`, and unknown fields MUST all serialize exactly as `{"code":"invalid_request","message":"invalid request"}`.

The snapshot route MUST map any unexpected monitor exception to status `500` with the exact sanitized JSON `HttpError(code="monitor_snapshot_failed", message="monitor snapshot failed")`; the exception text MUST NOT appear in the response.

The state-changing pgAdmin and runtime routes MUST require an explicit exact same-origin `Origin`, an absent-or-`same-origin` `Sec-Fetch-Site` value, `Content-Type: application/json`, and a per-session double-submit CSRF cookie/header before decoding or delegation. Any failed boundary check MUST use the same fixed `invalid_request` response and MUST NOT call the opener or runtime controller.

#### Scenario: Snapshot OpenAPI operation is stable

- **WHEN** `create_app(headless=True).openapi()` is inspected
- **THEN** `/api/v1/snapshot` has operation ID `getMonitorSnapshot`, references named `Snapshot` and `HttpError` component schemas, and describes exact nullable fields and enum values

#### Scenario: Snapshot runtime response is not a CLI envelope

- **WHEN** the snapshot route successfully serializes a `Snapshot`
- **THEN** the JSON body begins with the snapshot fields and contains no `ok`, `command`, `result`, or `error` envelope fields

#### Scenario: Unexpected monitor failures are sanitized

- **WHEN** the monitor raises an unexpected exception containing private paths or secret-like text
- **THEN** the route returns `500 application/json` with exactly `{"code":"monitor_snapshot_failed","message":"monitor snapshot failed"}` and does not expose the exception text

### Requirement: One msgspec-to-OpenAPI schema bridge

All frontend-facing request, success, and known-error models MUST have one canonical typed Python definition. The HTTP adapter MUST derive OpenAPI component schemas from `msgspec` JSON Schema and centrally translate definitions/references into OpenAPI components when required by FastAPI. It MUST NOT maintain a parallel handwritten Pydantic DTO hierarchy or route-local schema copies.

A contract test MUST serialize representative Python values through the production `msgspec` response path and validate the resulting JSON against the exact component/response schema published by `create_app(...).openapi()` for snapshot, pgAdmin and runtime action success/error payloads.

#### Scenario: Runtime payload validates against published schema

- **WHEN** representative snapshot, pgAdmin result, runtime action result, and known error values are serialized by the production encoder
- **THEN** each JSON value validates against the OpenAPI schema referenced by its operation response
- **AND** both `POST /api/v1/runtime/start` and `POST /api/v1/runtime/stop` `200` bodies validate as `RuntimeActionResult`
- **AND** their `404`, `409`, `422`, and `503` bodies validate as the named `HttpError` response schema

#### Scenario: Invalid request runtime payload is canonical

- **WHEN** malformed JSON, missing/non-string `environment_id`, or an unknown field is posted to the production route
- **THEN** each serialized `422` response validates against the operation's named `HttpError` schema, uses `HttpErrorCode.invalid_request`, and the pgAdmin opener is not called

#### Scenario: Browser request boundaries reject unsafe calls

- **WHEN** a pgAdmin request has no Origin, a foreign Origin, a non-JSON content type, cross-site Fetch Metadata, or a missing/mismatched CSRF token
- **THEN** it returns the fixed `422 invalid_request` JSON and the pgAdmin opener is not called

#### Scenario: Schema names are unique and reusable

- **WHEN** OpenAPI components are generated twice in one process
- **THEN** each public model has one stable component name and all `$ref` values resolve without route-specific duplicates

### Requirement: Headless mode excludes state-changing integration

Headless app composition MUST register the read-only snapshot and health operations but MUST NOT register `POST /api/v1/pgadmin/open`, `POST /api/v1/runtime/start`, or `POST /api/v1/runtime/stop`, construct a pgAdmin integration, create pgAdmin files, inspect/start a pgAdmin container, or mutate Docker state. Default local UI composition MUST retain its app-lifetime `OdooClient`/environment resource and bind `open_pgadmin`; runtime mutations MUST retain a separate lazily constructed worker-owned client to preserve catalog thread affinity. Schema export MUST use inert integration dependencies and retain its no-side-effect behavior. Local UI mode MUST register pgAdmin and runtime operations before mounting the SPA; external UI mode MUST omit the loopback-only pgAdmin operation while retaining protected runtime operations. Schema export MUST inject both an inert opener and an inert runtime controller.

#### Scenario: Headless schema has no pgAdmin operation

- **WHEN** `create_app(headless=True).openapi()` is generated
- **THEN** all three mutation paths are absent and no pgAdmin or runtime lifecycle side effect occurs

#### Scenario: UI schema includes pgAdmin operation without built assets

- **WHEN** `create_app(headless=False, static_assets=False, monitor=<schema stub>, pgadmin_opener=<typed no-op>, runtime_controller=<typed no-op>).openapi()` is generated in a clean checkout where `web/dist` is absent
- **THEN** pgAdmin and runtime operation paths are present without checking/mounting source-tree assets, constructing runtime resources, creating credentials, inspecting Docker, binding a socket, or opening a browser

#### Scenario: Runtime UI still requires packaged assets

- **WHEN** normal UI runtime composition uses its default static-assets setting and packaged `web/dist` is absent
- **THEN** app construction retains the existing actionable missing-assets failure rather than silently serving an API-only UI

### Requirement: Project-owned runtimes use the canonical dashboard snapshot

`GET /api/v1/snapshot` SHALL serialize project-owned runtime data through the same public typed `Snapshot` model and msgspec/OpenAPI bridge as environment-owned data. The API SHALL retain readiness, URL, database, PID/process count, CPU, RAM, and applicable cluster metrics. The JavaScript client and dashboard SHALL render a project runtime even when its environments array is empty. The compact dashboard SHALL display only the selected fields defined in web-sdk-codegen; PID/process count and cluster diagnostics SHALL remain API/CLI data. No parallel endpoint or frontend-only response shape SHALL be introduced.

#### Scenario: API serializes project-only state
- **WHEN** the monitor returns an initialized project with a live project-owned runtime and no environments
- **THEN** the API response validates against the published Snapshot schema and exposes that runtime without a synthetic environment

#### Scenario: Dashboard renders project runtime
- **WHEN** the generated client receives the project-only snapshot
- **THEN** the dashboard shows the running instance and its metrics instead of the no-environments empty state

### Requirement: Explicit trusted external proxy mode
`create_app()` and `run_server()` SHALL accept a typed external proxy configuration containing exact allowed external Hosts and exact trusted proxy addresses. Local mode SHALL retain loopback-only Host and bind behavior. External mode SHALL accept only direct connections from configured proxies, validate the forwarded HTTPS scheme/host against the configured external origin, reject unknown Host, malformed forwarding chains, and direct untrusted clients, and keep the backend endpoint non-public.

#### Scenario: Request arrives through the trusted Caddy proxy
- **WHEN** the peer is trusted and forwarded host/proto match the configured panel HTTPS origin
- **THEN** the SPA and API request are accepted with the configured external origin used consistently for mutation checks

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


### Requirement: Typed SDK-backed runtime HTTP operations

Non-headless composition SHALL register `POST /api/v1/runtime/start` with operation ID `startRuntime` and `POST /api/v1/runtime/stop` with operation ID `stopRuntime`. Their shared frozen, unknown-field-forbidden msgspec body `RuntimeActionRequest` SHALL contain exactly `owner_kind: Literal["project", "environment"]` and non-empty `owner_id: str`. Raw strict decoding SHALL reject malformed JSON, missing fields, wrong types, unknown fields and unsupported owner kinds with the fixed `422 invalid_request` payload before delegation.

The runtime controller SHALL resolve a project from its registered catalog repository and `ProjectConfig`, or resolve an environment through the public environment resource. It SHALL construct the corresponding public instance, execute `run_detached_command().run()` for start and `stop_runtime_command().run()` for stop, and preserve dependency preflight, immutable launch plans, artifact locks and captured process-ownership checks. It SHALL NOT accept browser-provided paths, argv, ports, database choices or credentials, bypass SDK safeguards, or implicitly publish/unpublish an instance. Start SHALL reject a selected runtime already observed as ready/not-ready. Dashboard mutations SHALL be serialized on one worker so the lazily constructed SDK client's SQLite connection is used on its owning thread without blocking the HTTP event loop.

Success SHALL be direct msgspec JSON `RuntimeActionResult` with exactly `state: Literal["started", "stopped"]`, described by named OpenAPI schemas. `started` SHALL mean the detached launch returned after proving the child alive, not that Odoo passed readiness; readiness SHALL continue to come from subsequent snapshots. Lookup failure SHALL return `404 runtime_not_found`; expected SDK/revalidation conflicts SHALL return `409 runtime_conflict`; unexpected failure SHALL return `503 runtime_unavailable`. Every error SHALL use named `HttpError` and contain no raw exception details.

#### Scenario: Registered owner is controlled through SDK

- **WHEN** a valid project or environment identity is submitted through the browser mutation boundary
- **THEN** only its public SDK detached-launch or ownership-checked stop command executes, and the response validates against RuntimeActionResult

#### Scenario: Duplicate start or unsafe stop is rejected

- **WHEN** the target is already running or the SDK cannot prove a safe lifecycle operation
- **THEN** the operation returns 409 runtime_conflict without bypassing ownership or signaling an unrelated process

#### Scenario: Request cannot supply launch configuration

- **WHEN** a request includes arbitrary path, argv, port, database or credential fields
- **THEN** strict decoding returns 422 invalid_request and no SDK lifecycle command executes

#### Scenario: External runtime mutation has one effective origin

- **WHEN** a trusted proxy forwards a request for the configured HTTPS panel host
- **THEN** Start/Stop are available only with an exact configured HTTPS Origin using default port 443, secure strict-same-site CSRF cookie/header and absent-or-same-origin Fetch Metadata
- **AND** wrong Origin scheme/host/port or CSRF returns 422; untrusted proxy or invalid forwarded Host/proto is rejected with 400 before delegation

#### Scenario: Headless runtime controls are absent

- **WHEN** the monitor is composed with headless=True
- **THEN** both runtime action routes are absent and no runtime controller resource is constructed

### Requirement: Authenticated Admin shares the monitor application boundary

Non-headless monitor composition SHALL mount Starlette Admin at `/admin`, add a visible same-origin Admin link to the monitor, and preserve the existing loopback Host or exact trusted-proxy boundary around both applications. Every Admin page, data request, and mutation SHALL require the configured authenticated session. Headless mode SHALL expose neither Admin routes nor Admin dependencies, secret-store writes, or session configuration. The existing monitor snapshot and runtime controls SHALL remain usable without an Admin session.

#### Scenario: Local administrator follows the monitor link

- **WHEN** a non-headless local monitor has valid administrator configuration
- **THEN** its Admin link SHALL open the package login flow and authenticated requests SHALL remain within the same Host boundary

#### Scenario: Headless composition excludes Admin

- **WHEN** `create_app(headless=True)` is composed or its OpenAPI schema is generated
- **THEN** no `/admin` route, login/session middleware, secret mutation, or Admin dependency initialization SHALL occur

#### Scenario: Unauthenticated request is denied

- **WHEN** a request reaches any Admin page or data action without a valid signed session
- **THEN** it SHALL be redirected to login or denied without returning catalogue or secret data

### Requirement: Admin catalogue resources are strictly read-only safe projections

Admin SHALL expose exactly three catalogue resources: backups with event detail, incomplete operations/errors, and environment history. These resources SHALL support bounded pagination, stable ordering, and only the specified typed filters. They SHALL expose no create, update, delete, bulk mutation, import, export, restore, raw SQL, or arbitrary filter/sort action.

Public/Admin DTOs SHALL be allowlisted and SHALL exclude raw paths, repository roots, configuration/runtime/applied-settings JSON, command output, raw exception text, secrets, and stored raw messages. Safe error text SHALL pass through the existing sanitizer and use a bounded generic value when no safe text remains.

#### Scenario: Backup list and event detail are bounded

- **WHEN** an authenticated administrator filters backups by project, source, database, status, or download-date range and opens one detail
- **THEN** the page SHALL return a bounded stable result with safe backup fields and only allowlisted events for that backup

#### Scenario: Error view cannot prove process liveness

- **WHEN** an operation has a stored running/nonterminal state
- **THEN** Admin SHALL label it as recorded incomplete state and SHALL NOT claim that a live process was observed

#### Scenario: Mutation routes are absent

- **WHEN** an authenticated client attempts create, edit, delete, import, export, or restore against a catalogue resource
- **THEN** the action SHALL be absent or denied before any catalogue mutation

#### Scenario: Unsafe persisted values are projected safely

- **WHEN** catalogue rows contain an absolute path, configuration JSON, command output, or secret-like error text
- **THEN** none of those raw values SHALL appear in HTML, JSON, logs, OpenAPI examples, or generated frontend values

### Requirement: Admin and monitor dependencies remain optional

Core SDK imports, CLI help, and non-dashboard operations SHALL NOT import or require FastAPI, Starlette Admin, session/auth packages, SQLCipher, Uvicorn, React assets, or generated dashboard code. Installing the supported dashboard extra SHALL provide one compatible Admin/auth/SQLCipher dependency set and packaged assets. Missing optional support SHALL yield actionable bounded errors only when the affected dashboard or encrypted-store capability is invoked.

#### Scenario: Core-only installation stays lightweight

- **WHEN** the package is installed without the dashboard extra
- **THEN** core imports and CLI help SHALL succeed without importing any Admin or SQLCipher module

#### Scenario: Packaged dashboard contains Admin support

- **WHEN** the built wheel is installed with its dashboard extra
- **THEN** monitor/Admin composition, templates/assets, password verification, and encrypted-store opening SHALL be available without source-tree files

### Requirement: Built-in Admin navigation names every registered entity

Authenticated Admin SHALL register exactly the existing five model views directly with Starlette Admin: Backups, Incomplete operations, Environment history, Remote Odoo secrets, and GitLab member secrets. Each SHALL define a nonblank built-in `menu_label` and icon. Navigation SHALL use the built-in sidebar and default index; it SHALL NOT add a custom dashboard, template, navigation component, ORM layer, or sixth entity.

#### Scenario: Authenticated sidebar lists all entities

- **WHEN** an authenticated administrator opens `/admin/`
- **THEN** the built-in sidebar and default index contain distinct links/panels for all five entities with human-readable labels

#### Scenario: Existing authorization still guards navigation

- **WHEN** an unauthenticated client requests the Admin root or any of the five view routes
- **THEN** the existing login redirect or denial occurs before catalogue or secret data is returned

### Requirement: Secret Admin routes use complete safe record identity

Remote Odoo and GitLab secret views SHALL use a hidden safe `admin_id` as Starlette Admin `pk_attr`. It SHALL encode the complete normalized public identity and current positive revision for exact list/detail/edit/delete routing: member ID plus host for GitLab, and base URL for remote Odoo. It SHALL contain no secret or secret-derived metadata and SHALL be excluded from list, detail, create, edit, import, and export fields.

#### Scenario: Created GitLab secret opens from its list row

- **WHEN** an administrator creates a GitLab secret and follows its built-in list-row detail action
- **THEN** the view resolves the exact `(member_id, host)` record and renders only safe identity/metadata fields

#### Scenario: Same member on two hosts routes independently

- **WHEN** two GitLab records share a member ID on different normalized hosts
- **THEN** each list/detail/edit/delete URL resolves only its own host-qualified record

#### Scenario: Revision changes after edit

- **WHEN** a valid secret edit increments the record revision
- **THEN** subsequent built-in navigation uses the new `admin_id`, while an old identity is rejected as stale or not found without overwriting newer data

#### Scenario: Invalid identity fails closed

- **WHEN** a secret detail, edit, or delete route receives malformed, incomplete, or non-positive-revision identity
- **THEN** the request returns bounded not-found/conflict behavior and performs no secret-store mutation
