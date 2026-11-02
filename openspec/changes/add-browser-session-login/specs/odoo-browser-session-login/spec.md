## ADDED Requirements

### Requirement: Eligible users are discovered through one bounded SDK query

The SDK SHALL expose an immutable command and delegating operation that resolve the selected ready local runtime and its one permitted database, accept an optional search string of at most 128 characters, and return at most 100 active internal users ordered by ID. Results SHALL exclude shared/portal/public users and `SUPERUSER_ID` and SHALL expose only numeric ID, login, and display name. Browser input SHALL NOT choose database, config, source, executable, addons paths, or Python/SQL code.

#### Scenario: Query eligible users

- **WHEN** an operator queries a ready compatible runtime with an optional search value
- **THEN** fixed SDK-owned ORM source returns matching eligible users without starting or mutating the runtime
- **AND** passwords, hashes, API keys, and unrelated partner data are neither read nor returned

#### Scenario: Runtime is unsupported

- **WHEN** the runtime is stopped, foreign, incompatible, or does not resolve exactly one permitted database
- **THEN** discovery returns a sanitized failure without choosing replacement state or starting the runtime

### Requirement: SDK session creation uses a private one-use SID handoff

The SDK SHALL expose an immutable session-creation command that revalidates a positive selected user, the same runtime and single database, and a supported local Odoo 19 filesystem session store. It SHALL create a non-`su` session through Odoo session/token APIs and SHALL transfer SID only through an SDK-owned 0700 directory and exclusive 0600 regular file that is read once without following symlinks and removed in `finally`. SID and session token SHALL be absent from every ordinary projection.

#### Scenario: Create selected-user session

- **WHEN** the captured compatible runtime and eligible user remain valid
- **THEN** one Odoo session is saved for that user/database without changing passwords, MFA settings, modules, source, or runtime state
- **AND** only the in-process consume response receives SID for cookie construction

#### Scenario: Creation or handoff fails

- **WHEN** the store is unsupported, the user becomes ineligible, execution times out, or private handoff validation fails
- **THEN** the operation fails closed, removes temporary credentials, and best-effort removes a known newly-created session
- **AND** its error contains no credential value or raw private path/output

### Requirement: Panel issues a minimal one-time bound handoff

`POST /api/v1/odoo/users/query` SHALL expose the bounded SDK result. `POST /api/v1/odoo/login-tickets` SHALL accept only owner kind, owner ID, and positive user ID through the existing panel authorization/CSRF boundary. It SHALL return an HTTPS action URL from current publication state, method `POST`, a random handoff with at least 256 bits of entropy, and expiry 30 seconds after issuance with `Cache-Control: no-store`. The process-local locked map SHALL bind owner, current runtime/database/publication host, selected user, and panel Origin. Issuance SHALL NOT create an Odoo session.

#### Scenario: Issue a current handoff

- **WHEN** an authorized operator selects a user for a ready uniquely resolved published runtime
- **THEN** the backend stores one expiring binding and returns its handoff only in the authenticated no-store response for immediate transient form submission

#### Scenario: Handoff expires or backend restarts

- **WHEN** its deadline passes or the monitor process restarts
- **THEN** consume cannot use it and no session has been created by issuance

### Requirement: Consume is atomic and bound to the instance host

`POST /_odcli/auth/consume` SHALL accept a bounded form body containing exactly one handoff value. It SHALL require a trusted configured proxy peer, authoritative HTTPS and instance Host, and the exact bound non-null panel Origin. It SHALL atomically remove a valid handoff before SDK work, re-resolve the same owner/runtime/database/publication and user eligibility, and reject expiry, replay, mismatch, extra fields, direct-backend access, GET, and HEAD without creating a session.

#### Scenario: Consume once successfully

- **WHEN** one valid current handoff reaches its bound instance host
- **THEN** exactly one request invokes session creation, sets host-only `session_id` with `Path=/; Secure; HttpOnly; SameSite=Lax` and no `Domain`, and returns 303 to fixed `/odoo`
- **AND** the response is `no-store` and does not expose the credential value

#### Scenario: Replay or boundary mismatch

- **WHEN** the handoff is replayed or its Origin, host, proxy, owner, runtime, database, publication, or user eligibility no longer matches
- **THEN** consume returns one sanitized invalid/unavailable classification and cannot create a replacement session or start/reconfigure the runtime

### Requirement: Open as is a transient explicit UI action

For every available published project/environment, React SHALL preserve **Open Odoo** and provide **Open as...**. The dialog SHALL perform bounded user queries, display login and name, require explicit selection/confirmation, synchronously open a blank target, request a handoff, submit it through a transient form, and remove credential-bearing form/input/local state on success or failure. It SHALL not put credentials in URL, storage, console, analytics, or durable React state and SHALL disclose shared-cookie behavior for tabs on one host.

#### Scenario: Confirm selected user

- **WHEN** the operator selects an eligible user and confirms
- **THEN** the pre-opened target receives the form POST and reaches `/odoo` as that user
- **AND** duplicate submission is disabled and transient credential state is removed

#### Scenario: Query or issuance fails

- **WHEN** user query, popup acquisition, or handoff issuance fails
- **THEN** the UI creates no session through that failed path, closes an unused blank tab when possible, and shows only a sanitized error

### Requirement: Operator documentation states the narrow support contract

Documentation SHALL describe the protected operator/proxy boundary, local Odoo 19 filesystem-store and single-database support, process-local handoff limitation, eligible-user rules, shared-host cookie behavior, normal logout, unsupported cases, credential exclusions, and the irreducible session-created/response-not-acknowledged window.

#### Scenario: Operator evaluates support

- **WHEN** an operator reads the feature documentation
- **THEN** the prerequisites, limitations, failure behavior, and residual risk are clear without inspecting implementation code
