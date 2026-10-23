## ADDED Requirements

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
