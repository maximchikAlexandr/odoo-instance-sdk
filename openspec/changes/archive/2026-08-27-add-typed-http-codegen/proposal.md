## Why

The dashboard currently exposes one untyped FastAPI route while React maintains a second handwritten copy of every Python snapshot model. Adding a controlled pgAdmin operation creates the second frontend-facing endpoint and makes a stable typed HTTP contract plus deterministic generated Fetch SDK the smallest reliable boundary between the Python resources and React.

## What Changes

- Split the dashboard adapter into small app composition and monitor router modules while preserving the existing CLI/public boot path and lazy optional imports.
- Publish stable snapshot and pgAdmin HTTP operations with explicit operation IDs, named success/error schemas, exact nullability/enums, sanitized failures, and a runtime-payload/OpenAPI contract test.
- Add a backend-resolved `POST /api/v1/pgadmin/open` operation for eligible SDK-owned healthy Compose PostgreSQL databases and expose per-environment pgAdmin eligibility as snapshot schema v3, preserving every MYL-55 v2 field and semantic.
- Manage one digest-pinned, loopback-only user pgAdmin container in supported single-user/no-login mode, with deterministic non-secret identity, password-file wiring, replace-on-start declarative servers, a global lifecycle lock, and private UID-readable generated files; omit the state-changing route in headless mode.
- Export canonical OpenAPI directly from a UI-route/schema-only `create_app(...).openapi()` composition that does not require built SPA assets, then generate a checked-in, deterministic Fetch SDK under `web/src/generated/` with exact-pinned Hey API tooling.
- Replace handwritten React API models and endpoint-specific `fetch()` wrappers with generated models/operations plus one handwritten base-URL configuration module, and add the pgAdmin button to environment cards.
- Add `make web-codegen` and `make web-codegen-check`, with CI/package verification for stale or nondeterministic OpenAPI/generated output.

## Capabilities

### New Capabilities

- `dashboard-http-api`: Modular FastAPI composition and the stable, schema-checked frontend HTTP contract.
- `local-pgadmin`: Eligibility, secure lifecycle, and typed open operation for the single shared local pgAdmin container.
- `web-sdk-codegen`: Deterministic OpenAPI export and checked-in generated TypeScript Fetch SDK.

### Modified Capabilities

- `environment-monitor`: Extend each environment snapshot with exact pgAdmin eligibility/state consumed by the dashboard.

## Impact

Affected areas are `src/odoo_instance_sdk/internal/serve.py`, new `src/odoo_instance_sdk/http/` adapters, public snapshot/result models and monitor collection, Docker/credential helpers, `src/odoo_instance_sdk/web/`, `Makefile`, CI, package artifacts, and backend/frontend/contract tests. FastAPI/Uvicorn remain dashboard-only optional dependencies; the only new Node development dependency is an exact-pinned OpenAPI generator. Implementation must begin after MYL-55 is merged or rebased into the feature branch; MYL-55 snapshot v2 (`observed_port` and `artifacts`) is the baseline and this change performs only the additive v2→v3 `pgadmin` migration.
