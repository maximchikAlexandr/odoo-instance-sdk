## ADDED Requirements

### Requirement: Browser-login HTTP routes remain typed and narrowly exposed

The user-query and handoff-issuance panel routes SHALL use the existing msgspec-to-OpenAPI bridge, generated client, exact-origin JSON check, and double-submit CSRF protection. The consume route SHALL accept only the exact instance-host form navigation and SHALL NOT relax any other panel Host, CSRF, content-type, CORS, static, or API boundary. Dashboard dependencies SHALL remain optional and lazily imported.

#### Scenario: Generated panel contract

- **WHEN** the dashboard OpenAPI document is generated
- **THEN** the user-query and handoff operations reference named unknown-field-forbidden request, success, and `HttpError` schemas
- **AND** React uses the generated contracts rather than handwritten duplicates

#### Scenario: Instance host requests another panel path

- **WHEN** an instance host requests another dashboard API/static path or a non-POST consume method
- **THEN** it reaches no management operation and creates no Odoo session

#### Scenario: Core-only import

- **WHEN** the SDK is imported without dashboard dependencies
- **THEN** the new SDK models and commands do not import FastAPI, Starlette, Uvicorn, or React assets
