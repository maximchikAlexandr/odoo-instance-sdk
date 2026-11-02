## Context

The dashboard currently opens `publication.external_url` in a new tab. FastAPI already resolves project/environment owners through `RuntimeController`, protects JSON mutations with exact-origin and double-submit CSRF checks, runs blocking SDK work away from the event loop, and generates a typed OpenAPI client. Publication already renders one validated, locked OdCLI-owned Caddy route file. `OdooInstance.run_shell_script_command()` provides the correct runtime Python/config/addons/OS context, but its generic JSON/stdout result is not safe for a session identifier.

Odoo 19 stores browser sessions in `odoo.http.root.session_store`; a valid session binds the database, user, context, and computed session token. The browser can receive a host-only cookie only from the published instance host, not from the panel host. The design therefore separates panel authorization, a one-time cross-origin navigation credential, and session creation at consume time.

## Goals / Non-Goals

**Goals**

- Let a trusted dashboard operator select an active internal user for a published project or environment and open `/odoo` as that user without passwords or an installed Odoo module.
- Bind every handoff to the current owner, runtime, database, publication host, panel origin, and selected user; fail closed when any binding changes.
- Keep ticket, SID, session token, passwords, and cookie values out of URLs, plans, fingerprints, ordinary JSON, events, recordings, logs, audit, and evidence.
- Preserve the SDK-first immutable command boundary, optional dashboard dependencies, Caddy recovery semantics, and existing Open Odoo behavior.

**Non-goals**

- Remote Odoo without server-side session-store access, arbitrary Odoo versions/session stores, portal/public/superuser login, per-tab cookie isolation, automatic switch-back, SSO, or new RBAC.
- Installing/updating an Odoo module, changing Odoo source or passwords/MFA settings, starting a stopped runtime, or choosing among ambiguous databases.
- Redis, multiple FastAPI workers, a durable ticket database, a new daemon, a new CLI leaf, or fixing unrelated dashboard packaging/update behavior.

## Ponytail full gate

| Proposed subsystem | Necessity | Existing alternative | Decision |
| --- | --- | --- | --- |
| SDK user/session primitives | HTTP must not own fixed Odoo scripts or runtime resolution | Generic shell command leaks domain and secret transport concerns into HTTP | Keep two narrow primitives with delegating command siblings |
| Closed SID handoff | SID cannot use stdout or normal command result projections | Existing owner-only temp-file/action-step patterns | Keep one scoped 0700 directory/0600 no-follow file, always cleaned in `finally` |
| Login ticket store | A panel-host page cannot set a host-only instance cookie | URL/query SID, durable DB, Redis | Keep bounded locked in-memory store for the existing single-process monitor |
| Consume transport | Cookie must originate on the instance host | CORS, `document.cookie`, query credentials | Keep one exact form POST path with dedicated checks |
| Caddy route branch | The exact consume path must reach FastAPI on the instance host | Expose the whole panel API or add an Odoo module | Keep one mutually exclusive exact-path route before Odoo proxying |
| UI dialog | Operator must select and confirm a user | Existing Open Odoo button has no selection state | Keep a focused dialog; no general component framework |

No generic runner, service layer, repository, credential framework, router registry, or authentication platform is introduced.

## Decisions

### D1: SDK owns fixed scripts and eligibility

Add immutable public models for `BrowserLoginUser`, `BrowserLoginUserPage`, and a secret-bearing `BrowserSessionGrant` whose public projection contains only user/database metadata. `OdooInstance.list_browser_login_users_command()` and `create_browser_session_command()` build one frozen command snapshot; convenience methods only call the sibling and `run()`.

The list script uses an ORM domain assembled inside SDK-owned source, accepts only typed argv values, sorts by ascending ID, reads `limit + 1`, and returns active non-share users excluding `SUPERUSER_ID`. `base.user_admin` marks the default only when it resolves to an eligible user. Session creation rechecks eligibility and runtime/session-store compatibility, constructs the Odoo 19 session without `su`, computes its token through Odoo APIs, and records an audit-safe outcome.

Alternative: put scripts in FastAPI. Rejected because it duplicates runtime resolution and violates the SDK-first boundary.

### D2: SID crosses the child boundary through an owned temporary file

The parent creates a private directory and exclusive regular file contract before execution. The child opens without following symlinks, writes only the SID with owner-only permissions, and emits safe metadata on the framed result channel. The SDK reads once and deletes in `finally`; creation, write expectation, read, deletion, and best-effort orphan-session cleanup are honest mutating action steps. Database `commit=False` remains correct, but the command is always `mutating=True` because the session store is external to the PostgreSQL rollback.

The public plan, result projection, fingerprint, repr, error, events, recordings, stdout, and stderr never contain the SID, secret-file contents, ticket, or session token. Failed transfer attempts best-effort delete a created session when its SID is known. A server cannot prove that the browser received a `Set-Cookie`; this residual disconnect window is documented and tested to the observable boundary.

### D3: Ticket issuance is cheap; consume performs authoritative work

`POST /api/v1/odoo/users/query` resolves the owner and executes the list primitive. `POST /api/v1/odoo/login-tickets` verifies authorization, current runtime/publication identity, one unambiguous database, positive user ID, and capacity, but does not create a session or run another user lookup.

The store keeps only a cryptographically random secret digest/key and immutable bindings for 30 seconds. It is bounded, lock-protected, single-process, and restart-invalidated. Consume atomically moves `pending` to `consuming`; every attempted consume is terminal, including SDK failure. Capacity failure is 429. Multi-worker startup/configuration SHALL reject enabling this feature until a shared atomic store is deliberately designed.

Alternative: create the session during ticket issue. Rejected because unused/popup-blocked tickets would create orphan sessions.

### D4: Consume is a dedicated navigation boundary

`POST /_odcli/auth/consume` accepts an size-bounded `application/x-www-form-urlencoded` body with exactly one `ticket` field. It does not reuse or weaken JSON `request_is_safe()`. Before consuming it verifies an approved proxy peer, authoritative publication host and HTTPS projection, exact non-null panel `Origin`, expected ticket binding, and current owner/runtime/database/publication identity. Forwarded headers are trusted only from configured peers. GET/HEAD return 405 without consuming.

On success FastAPI sets `session_id` without `Domain`, with `Path=/; Secure; HttpOnly; SameSite=Lax`, adds `Cache-Control: no-store` and `Referrer-Policy: no-referrer`, and returns 303 to the fixed `/odoo`. It never accepts a return URL, database, config path, source, executable, or host from the browser.

### D5: Caddy exposes one exact path, not the dashboard API

Each published instance route keeps its Basic Auth policy and contains a mutually exclusive exact `POST /_odcli/auth/consume` branch to the configured live monitor endpoint before the general Odoo branch. Other methods at that path and all other `/api` paths remain unavailable through the instance host. The branch forwards the authoritative host/proto and client address according to existing trusted-proxy rules. If the monitor endpoint is unavailable, the feature fails without rerouting to Odoo or changing publication state.

The route model captures the monitor endpoint at publication/reconciliation time; candidate validation, lock, atomic replacement, reload, and rollback remain unchanged.

### D6: Typed HTTP/OpenAPI and bounded execution

Unknown fields are rejected. Panel endpoints use the existing exact-origin JSON/CSRF gate; consume uses its dedicated gate. `HttpErrorCode` gains the browser-login codes specified in the delta. Known failures map to bounded messages and no raw exception. SDK calls run in the existing bounded worker mechanism with a deadline and queue/capacity control; catalog access remains on its owning thread and no callback invokes a nested `Command.run()`.

Audit records time, correlation ID, owner/runtime identity, database, selected numeric user ID, available verified actor or honest shared/local principal, and sanitized outcome. It excludes credential values and full headers.

### D7: React submits a transient form to a synchronously opened tab

The existing Open Odoo control remains. **Open as...** is enabled only for an available publication and live monitor capability. Opening/searching the dialog calls users/query with debouncing and keyset pagination; it displays name/login and preselects the eligible `base.user_admin` marker.

Confirmation synchronously opens a blank named target to satisfy popup policy, requests a ticket, creates a transient hidden POST form targeted at that tab, submits it, then removes form/input and clears the ticket variable. Failure closes the blank tab when possible. Ticket/SID never enter URL, React state persisted beyond submission, storage, analytics, or console. UI explains that all tabs for one instance host share the resulting cookie.

### D8: Verification separates deterministic coverage from live proof

Unit/contract tests cover models, commands, redaction, temp-file safety/cleanup, ticket state/concurrency/time, trust headers, errors, Caddy rendering, OpenAPI/codegen, UI behavior, and package boundaries. Existing dashboard CI gains those deterministic tests; real Odoo CI gains source-backed session construction/logout and secret-registry assertions where its container boundary is sufficient.

An independent Tester uses one fresh `odcli-N` checkout/database/port from the project-described GitLab test repository and the numbered profile. The live walkthrough uses the exact product SHA, a compatible Odoo 19 filesystem session store, two eligible users with different access rights, publication through the actual proxy, and a clean browser profile. Evidence records only identities, response status, safe session-info fields, sanitized audit outcomes, commands/exit codes, revisions, and images; credentials remain excluded.

## Risks / Trade-offs

- Odoo session internals or auth/session extensions may differ. Detect the supported filesystem-store/runtime contract and return `browser_login_unsupported`; do not guess or bypass extensions.
- Direct session creation intentionally bypasses password/MFA challenge. Limit it to an already trusted operator boundary and make impersonation explicit in audit.
- The single-process store forbids horizontal monitor workers. This is an explicit MVP limitation instead of an unneeded distributed store.
- A response disconnect after session creation can leave an orphan session. Delete best-effort on known pre-response failures and document the irreducible acknowledgement window.
- Caddy route changes couple instance and monitor availability. Exact-path routing and existing atomic rollback contain the impact.
- One host-only cookie means tabs on the same instance host switch together. The UI states this; per-tab isolation is out of scope.

## Migration / Rollback

The change is additive and requires no data migration. Existing publications are reconciled/republished to gain the consume branch. Rolling back removes the UI and route branch; backend restart invalidates outstanding tickets, and existing Odoo sessions remain governed by normal Odoo logout/expiry. No durable ticket state requires cleanup.

## Open Questions

None. Implementers SHALL use the contracts above; unsupported auth/session extensions fail closed rather than selecting a new mechanism.
