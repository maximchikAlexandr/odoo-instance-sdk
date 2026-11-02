## ADDED Requirements

### Requirement: Browser-login HTTP contracts remain typed and narrowly exposed

All browser-login panel requests, successes, and known errors SHALL use the existing canonical msgspec-to-OpenAPI bridge and generated-client contract. The panel user-query and ticket routes SHALL retain the existing exact same-origin JSON and double-submit CSRF protection. The instance-host consume route SHALL use a separate exact-path form-navigation boundary and SHALL NOT relax panel Host, CSRF, content-type, CORS, or static/API routing rules. Dashboard dependencies SHALL remain optional and lazily imported.

#### Scenario: OpenAPI exposes panel contracts

- **WHEN** the dashboard OpenAPI document is generated
- **THEN** user-query and login-ticket operations reference named unknown-field-forbidden request, success, and `HttpError` schemas
- **AND** generated TypeScript uses those schemas without a handwritten duplicate interface

#### Scenario: Instance host requests another panel API

- **WHEN** a request on a published instance host targets `/api/v1/snapshot`, runtime controls, admin routes, or any path other than exact consume
- **THEN** it cannot reach the dashboard API through the instance publication route

#### Scenario: Core-only import

- **WHEN** the SDK is installed without dashboard dependencies
- **THEN** importing core SDK and browser-session model/command APIs does not import FastAPI, Starlette, Uvicorn, or React assets
