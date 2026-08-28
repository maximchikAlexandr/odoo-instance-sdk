# web-sdk-codegen Specification

## Purpose
TBD - created by archiving change add-typed-http-codegen. Update Purpose after archive.
## Requirements
### Requirement: Canonical deterministic OpenAPI export

The repository MUST store canonical `openapi.json` exported directly from `create_app(headless=False, static_assets=False, monitor=<schema stub>, pgadmin_opener=<typed no-op>).openapi()` without starting an HTTP server. `static_assets=False` MUST preserve the UI API route set while skipping the packaged-SPA existence check and mount; normal runtime UI composition MUST keep static assets enabled by default. Export MUST use sorted object keys and deterministic formatting and MUST contain no timestamp, absolute path, environment value, random identifier, local port allocation, secret, or machine-specific server URL. Two consecutive exports from identical sources MUST be byte-identical.

The export path MUST work in a clean checkout with ignored `web/dist` absent and MUST perform no source-tree asset access, public resource construction, Docker inspection/invocation, credential/configuration creation, socket bind, server start, or browser open. The injected schema monitor/opener are inert typed test/export dependencies only.

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

Hey API adoption MUST pass all of these gates: stable operation names `getMonitorSnapshot` and `openPgAdmin`, exact nullability/enums, no Axios or framework hooks, no generated mocks, no custom templates, flat tree-shakeable callable operations, the required generated header, and byte-identical consecutive output. If any gate fails, the only permitted fallback is exact-pinned `openapi-typescript` plus `openapi-fetch` with the same output contract; no additional generator evaluation is allowed.

#### Scenario: Generated operations are callable

- **WHEN** generation completes from canonical OpenAPI
- **THEN** React can import callable Fetch operations for `getMonitorSnapshot` and `openPgAdmin` plus their generated request/response/error models

#### Scenario: Generation is deterministic

- **WHEN** SDK generation runs twice from unchanged `openapi.json` and lockfile
- **THEN** all files under `web/src/generated/` are byte-identical

### Requirement: React consumes only generated API contracts

React MUST remove handwritten API interfaces/types and endpoint-specific `fetch()` wrappers from `web/src/api.ts`. The only handwritten runtime client module MUST configure the generated Fetch client's relative `baseUrl`; UI polling, errors, and pgAdmin actions MUST invoke generated operations and use generated types. React MUST NOT reimplement Python eligibility, ownership, cluster-health, nullability, or enum invariants.

#### Scenario: No handwritten API model copy remains

- **WHEN** frontend sources are inspected after generation
- **THEN** snapshot, pgAdmin, and HTTP error types are imported from `web/src/generated/` and no equivalent handwritten interfaces or direct endpoint-specific `fetch()` call remains

#### Scenario: Environment card uses typed eligibility

- **WHEN** an environment card renders
- **THEN** its pgAdmin button enabled/disabled state and explanation come from the generated `pgadmin.state` field, and clicking an eligible button invokes generated `openPgAdmin`

### Requirement: Codegen commands and stale-output gate

The root Makefile MUST provide `make web-codegen` to export canonical OpenAPI and regenerate the SDK, and `make web-codegen-check` to run generation in a clean comparison flow and fail on any stale or nondeterministic `openapi.json` or generated file. The check MUST preserve the developer's working files. A clean-checkout CI job MUST run in this exact dependency/order boundary: install Python dashboard/test dependencies, run `npm ci`, run `make web-codegen-check` while `web/dist` is absent, run frontend tests, then `npm run build`; backend contract/package checks MAY follow but MUST NOT be a prerequisite that creates `dist` for codegen.

#### Scenario: Stale generated SDK fails verification

- **WHEN** a Python HTTP model or operation changes without regenerating committed output
- **THEN** `make web-codegen-check` exits non-zero and identifies generated artifacts as stale

#### Scenario: Clean generated SDK passes verification

- **WHEN** committed OpenAPI and generated files match two consecutive generation runs
- **THEN** `make web-codegen-check`, frontend tests, and frontend build exit zero
