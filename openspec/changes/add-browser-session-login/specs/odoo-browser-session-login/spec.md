## ADDED Requirements

### Requirement: Eligible browser-login users are discovered through a typed SDK operation

The public SDK SHALL expose immutable `list_browser_login_users_command()` and delegating `list_browser_login_users()` operations on `OdooInstance`. They SHALL use SDK-owned fixed source in the selected runtime, accept only `query` of at most 128 characters, positive `after_id`, `limit` from 1 through 100, and a bounded timeout, and SHALL return a typed page sorted by ascending user ID. The result SHALL include only active internal users, SHALL exclude share/public/portal users and `SUPERUSER_ID`, SHALL expose only ID, login, display name, and eligible `base.user_admin` marker, and SHALL resolve exactly one database without starting or mutating the runtime.

#### Scenario: Query eligible users

- **WHEN** an operator queries a ready compatible runtime with a search value and page limit
- **THEN** the SDK returns at most the requested users plus a keyset cursor derived from a `limit + 1` read
- **AND** the fixed ORM domain searches login and name without interpolating user-controlled Python or SQL

#### Scenario: Ineligible accounts are excluded

- **WHEN** matching users include inactive, share, portal, public, or superuser records
- **THEN** none of those records appears in the result
- **AND** no password hash, API key, partner detail, or credential is read or returned

#### Scenario: Runtime database is not unambiguous

- **WHEN** the selected runtime is stopped, foreign, incompatible, or resolves zero or multiple permitted databases
- **THEN** discovery fails with a typed sanitized result and does not choose a database or start the runtime

### Requirement: Browser sessions use a secret-safe mutating SDK operation

The public SDK SHALL expose immutable `create_browser_session_command()` and delegating `create_browser_session()` operations. The command SHALL revalidate the selected positive user ID, runtime/config/source/database identity, effective OS context, and compatible Odoo 19 filesystem session store; SHALL create a non-`su` session using Odoo session/token APIs; and SHALL be mutating even when its database transaction rolls back. Its public projection SHALL expose only safe user/database metadata. The SID and session token SHALL NOT occur in its plan, fingerprint, repr, JSON projection, stdout, stderr, events, recordings, logs, audit, or exception text.

#### Scenario: Create a session for an eligible user

- **WHEN** the selected compatible runtime and eligible active internal user still match the captured command
- **THEN** the SDK writes one valid Odoo session in that runtime's actual session store
- **AND** the safe result identifies the user and database without serializing the SID

#### Scenario: User becomes ineligible before creation

- **WHEN** the user is removed, archived, becomes shared, resolves to `SUPERUSER_ID`, or no longer belongs to the captured runtime before execution
- **THEN** session creation fails before saving a session

#### Scenario: Session backend is unsupported

- **WHEN** the runtime cannot prove compatible source/config/OS context and filesystem session-store access or an installed extension changes the required contract
- **THEN** the operation returns a sanitized unsupported result without changing auth settings, passwords, MFA, modules, source, or runtime state

### Requirement: Secret handoff and cleanup are fail closed

The session command SHALL transfer the SID only through an SDK-owned owner-only temporary location with a 0700 parent, exclusive 0600 regular file semantics, no symlink following, and unconditional cleanup in `finally`. The snapshot SHALL describe safe create/read/delete actions without file contents. A failed or timed-out handoff SHALL remove temporary credentials and SHALL best-effort delete a session created but not handed to HTTP when the SID is known.

#### Scenario: Child attempts an unsafe secret path

- **WHEN** the handoff target is a symlink, pre-existing foreign file, non-regular file, or has unsafe ownership or permissions
- **THEN** the command fails before consuming its contents and does not overwrite the target

#### Scenario: Execution fails after session save

- **WHEN** the child saves a session but safe handoff or result decoding fails
- **THEN** the SDK cleans the temporary location and attempts bounded deletion of the newly created session
- **AND** any cleanup failure remains sanitized and auditable without exposing the SID

### Requirement: Panel APIs issue one-time bound login tickets

`POST /api/v1/odoo/users/query` SHALL expose the SDK page through a typed unknown-field-forbidden request and response. `POST /api/v1/odoo/login-tickets` SHALL accept owner kind, owner ID, and positive user ID; SHALL require the existing panel authorization, exact-origin JSON and CSRF boundary; and SHALL return only an HTTPS action URL derived from current publication state, method `POST`, an opaque ticket with at least 256 bits of entropy, and an expiry 30 seconds after issuance. Responses and errors SHALL use `Cache-Control: no-store`.

#### Scenario: Issue a ticket for a current publication

- **WHEN** an authorized operator selects a positive user ID for a ready uniquely resolved published runtime and capacity is available
- **THEN** the backend stores immutable owner/runtime/database/host/origin/user/principal bindings and returns a short-lived ticket
- **AND** issuance does not create an Odoo session or run a second user lookup

#### Scenario: Ticket capacity is exhausted

- **WHEN** the bounded pending-ticket capacity is full after expired entries are removed
- **THEN** issuance returns 429 `browser_login_busy` without creating a ticket or session

#### Scenario: Backend restarts

- **WHEN** the single supported FastAPI process restarts
- **THEN** every previously issued ticket becomes invalid

### Requirement: Ticket consumption is atomic and bound to the instance host

`POST /_odcli/auth/consume` SHALL accept a bounded form body containing exactly one ticket and SHALL be reachable only through a configured trusted proxy for the ticket's current published instance host. It SHALL require authoritative HTTPS, exact non-null bound panel `Origin`, valid forwarded headers from an approved peer, and matching current owner/runtime/database/publication identity. It SHALL atomically move one pending ticket to consuming before SDK work; expiry, replay, concurrent consume, or any terminal attempt SHALL make it unusable. GET and HEAD SHALL return 405 without consuming.

#### Scenario: Consume once successfully

- **WHEN** a valid pending ticket arrives through the bound host and proxy while every captured identity still matches
- **THEN** exactly one request invokes session creation, sets a host-only `session_id` cookie with `Path=/; Secure; HttpOnly; SameSite=Lax` and no `Domain`, and returns 303 to `/odoo`
- **AND** the response includes `Cache-Control: no-store` and `Referrer-Policy: no-referrer`

#### Scenario: Concurrent replay

- **WHEN** two requests concurrently submit the same valid ticket
- **THEN** at most one enters session creation and every other request returns 410 `login_ticket_invalid`

#### Scenario: Bound state changes

- **WHEN** the owner, runtime identity, database, publication host, or user eligibility changes between issuance and consume
- **THEN** consume fails closed without selecting replacement state or starting a runtime
- **AND** the ticket cannot be retried

#### Scenario: Untrusted navigation boundary

- **WHEN** Origin is absent, null, or different; the peer/Host/proto/forwarded chain is untrusted; or the body contains extra fields
- **THEN** consume returns a sanitized refusal without creating a session, weakening panel CSRF, enabling wildcard CORS, or accepting browser-supplied target data

### Requirement: Browser-login failures and audit are sanitized

Known failures SHALL use `HttpError` and the codes `browser_login_forbidden`, `invalid_origin`, `runtime_not_found`, `odoo_user_not_found`, `runtime_not_ready`, `publication_unavailable`, `database_ambiguous`, `runtime_changed`, `odoo_user_ineligible`, `browser_login_unsupported`, `login_ticket_invalid`, `invalid_request`, `browser_login_busy`, `browser_login_unavailable`, and `browser_login_timeout` with the specified 403/404/409/410/422/429/503/504 classes. Audit SHALL record time, correlation ID, owner/runtime/database, numeric selected user ID, verified actor or honest local/shared principal, and sanitized outcome, but no ticket, SID, session token, password material, form body, or complete Cookie headers.

#### Scenario: Internal failure contains secrets

- **WHEN** SDK, Odoo, proxy, or decoding failure text contains paths, output, headers, or secret-like values
- **THEN** HTTP and audit return bounded actionable classifications without copying the raw value

### Requirement: Open as user is an explicit transient browser workflow

For each available published project or environment, React SHALL preserve **Open Odoo** and provide **Open as...**. The dialog SHALL query/search eligible users with bounded debouncing and keyset pagination, display name plus login, preselect an eligible `base.user_admin`, and require explicit confirmation. Confirmation SHALL synchronously open the target tab, request a ticket, submit it through a transient POST form to the returned action URL, and remove the form/input and ticket from memory on success or failure. It SHALL not place ticket or SID in URL, storage, durable React state, analytics, or console.

#### Scenario: Popup-capable confirmation

- **WHEN** an operator confirms an eligible user and ticket issuance succeeds
- **THEN** the already-opened target receives a form POST and navigates through consume to `/odoo`
- **AND** the originating UI removes transient credential-bearing DOM nodes and disables duplicate submission

#### Scenario: Ticket request fails

- **WHEN** the synchronous blank tab opened but ticket issuance fails
- **THEN** the UI closes that tab when possible and displays only a sanitized actionable error

#### Scenario: Tabs share one host cookie

- **WHEN** Open as changes the session for a host already open in another tab
- **THEN** the UI has disclosed that tabs for that host share the selected session while distinct instance hosts remain isolated

### Requirement: Execution is bounded without nested command ledgers

User discovery and session creation SHALL run through the existing bounded FastAPI worker boundary with explicit deadlines and capacity control. Catalog and runtime resolution SHALL preserve thread affinity, and a callback executing one command SHALL NOT invoke a nested `Command.run()` ledger.

#### Scenario: Session creation exceeds its deadline

- **WHEN** SDK execution exceeds the configured bounded deadline
- **THEN** the request returns 504 `browser_login_timeout`, the ticket stays consumed, temporary credentials are cleaned, and no event-loop or worker task survives unowned

### Requirement: Normal logout and MFA policy remain Odoo-owned

The created session SHALL obey normal Odoo logout and session-token validation. Platform impersonation SHALL not disable or rewrite MFA configuration and SHALL be explicit in audit; an incompatible auth/session extension SHALL produce `browser_login_unsupported`.

#### Scenario: Logout invalidates the session

- **WHEN** the browser completes the normal Odoo logout flow after Open as
- **THEN** the prior host-only cookie no longer authorizes `/web/session/get_session_info`

#### Scenario: User has MFA configured

- **WHEN** a compatible eligible user with MFA is selected through platform impersonation
- **THEN** the operation leaves MFA settings unchanged and records the impersonation outcome without claiming a password/MFA login challenge occurred
