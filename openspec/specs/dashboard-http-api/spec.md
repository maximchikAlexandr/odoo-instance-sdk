# dashboard-http-api Specification

## Purpose
TBD - created by archiving change add-typed-http-codegen. Update Purpose after archive.
## Requirements
### Requirement: Modular dashboard HTTP adapter

The dashboard HTTP adapter MUST be split into `odoo_instance_sdk.http.app` for `create_app`, loopback Host policy, health/static composition and server boot, and `odoo_instance_sdk.http.monitor` for the monitor `APIRouter`, snapshot serialization, and pgAdmin route composition. The existing `odcli monitor` boot contract and import path MUST remain compatible. Dashboard-only dependencies MUST remain optional and lazily imported; importing the core SDK or CLI help MUST NOT import FastAPI, Starlette, Uvicorn, React assets, or generated TypeScript.

The split MUST NOT introduce a generic HTTP service layer, dependency-injection container, router registry, or Click imports in route modules. Routes MUST delegate catalog, Git, Docker, database, and pgAdmin orchestration to the existing monitor/public resource boundary.

#### Scenario: Core-only import remains dependency-neutral

- **WHEN** the package is installed without the dashboard extra and `odoo_instance_sdk`, `odoo_instance_sdk.cli`, or CLI help is imported
- **THEN** the import succeeds without FastAPI, Starlette, or Uvicorn being installed

#### Scenario: Existing monitor boot path is preserved

- **WHEN** `odcli monitor` starts in UI or headless mode after the module split
- **THEN** it preserves the current loopback host validation, port selection, browser-open, health route, and SPA composition behavior

### Requirement: Stable typed snapshot operation

`GET /api/v1/snapshot` MUST publish operation ID `getMonitorSnapshot`, use the public `Snapshot` model as its named `200 application/json` schema, and publish a named `HttpError` response for status `500`. The optional `project_id: string | null` query parameter MUST retain the existing filter semantics. The response MUST be the direct `msgspec` JSON serialization of `Snapshot`, never a CLI JSON/TOON envelope.

`HttpError` MUST be a frozen, unknown-field-forbidden typed Python model with exact fields `code: HttpErrorCode` and `message: str`. `HttpErrorCode` MUST be a string enum covering `invalid_request`, `monitor_snapshot_failed`, `environment_not_found`, `pgadmin_not_eligible`, `database_not_found`, and `pgadmin_unavailable`. Error messages MUST be bounded and user-actionable but MUST NOT expose secrets, absolute/internal paths, raw exception text, Docker payloads, command output, decoder details, or submitted invalid values. `openPgAdmin` MUST publish a named `HttpError` response for status `422`; malformed JSON, missing/non-string `environment_id`, and unknown fields MUST all serialize exactly as `{"code":"invalid_request","message":"invalid request"}`.

The snapshot route MUST map any unexpected monitor exception to status `500` with the exact sanitized JSON `HttpError(code="monitor_snapshot_failed", message="monitor snapshot failed")`; the exception text MUST NOT appear in the response.

The state-changing pgAdmin route MUST require an explicit exact same-origin `Origin`, an absent-or-`same-origin` `Sec-Fetch-Site` value, `Content-Type: application/json`, and a per-session double-submit CSRF cookie/header before decoding or delegation. Any failed boundary check MUST use the same fixed `invalid_request` response and MUST NOT call the opener.

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

A contract test MUST serialize representative Python values through the production `msgspec` response path and validate the resulting JSON against the exact component/response schema published by `create_app(...).openapi()` for both snapshot and pgAdmin success/error payloads.

#### Scenario: Runtime payload validates against published schema

- **WHEN** representative snapshot, pgAdmin result, and known error values are serialized by the production encoder
- **THEN** each JSON value validates against the OpenAPI schema referenced by its operation response

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

Headless app composition MUST register the read-only snapshot and health operations but MUST NOT register `POST /api/v1/pgadmin/open`, construct a pgAdmin integration, create pgAdmin files, inspect/start a pgAdmin container, or mutate Docker state. Default UI composition MUST retain one `OdooClient`/environment resource for the app lifetime and bind its `open_pgadmin`; schema export MAY inject an inert opener and MUST retain its no-side-effect behavior. UI mode MUST register the pgAdmin operation before mounting the SPA.

#### Scenario: Headless schema has no pgAdmin operation

- **WHEN** `create_app(headless=True).openapi()` is generated
- **THEN** `/api/v1/pgadmin/open` is absent and no pgAdmin lifecycle side effect occurs

#### Scenario: UI schema includes pgAdmin operation without built assets

- **WHEN** `create_app(headless=False, static_assets=False, monitor=<schema stub>, pgadmin_opener=<typed no-op>).openapi()` is generated in a clean checkout where `web/dist` is absent
- **THEN** `/api/v1/pgadmin/open` is present without checking/mounting source-tree assets, constructing runtime resources, creating credentials, inspecting Docker, binding a socket, or opening a browser

#### Scenario: Runtime UI still requires packaged assets

- **WHEN** normal UI runtime composition uses its default static-assets setting and packaged `web/dist` is absent
- **THEN** app construction retains the existing actionable missing-assets failure rather than silently serving an API-only UI
