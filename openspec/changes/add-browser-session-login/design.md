## Context

The current React card already opens `publication.external_url`. FastAPI already has typed msgspec/OpenAPI routes, same-origin CSRF protection, a single-worker SDK executor, and trusted Caddy proxy validation. `RuntimeController` already resolves project/environment owners, `run_shell_script_command()` already supplies the correct Odoo Python/config/addons/OS context, and publication already renders a locked OdCLI-owned route file containing both instance and panel routes.

A panel subdomain cannot set a host-only cookie for an instance subdomain. Therefore a narrow instance-host POST and a short-lived one-time handoff are unavoidable. Everything around that seam should reuse existing code.

## Goals / Non-Goals

**Goals**

- Let a trusted panel operator search a bounded list of active internal users, select one, and open the published local Odoo 19 instance as that user.
- Create the real Odoo session only when the instance-host handoff is consumed; set a host-only secure cookie and redirect to `/odoo`.
- Keep passwords, SID, session token, and handoff value outside URLs, logs, retained UI state, ordinary JSON/results, Git, and evidence.
- Preserve the existing SDK command, HTTP/OpenAPI, publication, generated-client, and React patterns.

**Non-goals**

- General SSO/RBAC, remote Odoo, portal/public/superuser users, multiple databases, non-filesystem session stores, arbitrary Odoo versions, per-tab cookie isolation, automatic switch-back, or auth-extension compatibility guarantees.
- Server-side pagination, an admin-default heuristic, a new audit service, a distributed ticket store, multiple monitor workers, a new daemon/CLI command, an Odoo addon, or changes to passwords/MFA/modules/source/runtime state.

## Ponytail full gate

| Concern | Why it is needed | Existing alternative | Decision |
| --- | --- | --- | --- |
| User query | The browser must choose an eligible user without receiving DB/config/source controls | Existing SDK shell runner | Keep one bounded query; remove keyset pagination and admin-default logic |
| Session creation | Odoo has no suitable arbitrary-UID HTTP endpoint | Existing SDK shell runner in the runtime context | Keep one fixed SDK command and eligibility recheck |
| Private SID transfer | SID cannot use normal stdout/result/event projections | Existing owner-only temporary-file patterns | Keep one scoped file and unconditional cleanup; no reusable credential framework |
| Cross-host handoff | Only the instance host can set its host-only cookie | Query URL, panel cookie, or exposing SID are unsafe | Keep a tiny locked expiring map and exact form POST; remove capacity tiers, digests, and multi-worker machinery |
| Caddy branch | The exact consume POST must reach FastAPI on the instance host | Existing route file already contains the panel endpoint | Reuse that endpoint during rendering; do not add another publication service/model layer |
| UI | The operator needs selection and confirmation | Existing card/action and Mantine components | Keep one dialog with bounded search; no pagination framework or persistent state |
| Verification | A real cookie/session needs one real boundary proof | Existing unit/dashboard/real-Odoo CI and numbered slot | Keep focused tests and one fixture helper; remove the probe framework and exhaustive live fault matrix |

Removed from R3 as unjustified scope: keyset pagination, `base.user_admin` defaulting, ticket-capacity policy, explicit multi-worker refusal, a dedicated audit subsystem/correlation model, exhaustive runtime/publication drift classifications, MFA/extension certification, a separate probe driver, repository-wide credential projection matrices, and the seven-WP DAG.

## Decisions

### D1: Two narrow SDK operations reuse the shell runner

`list_browser_login_users_command(owner, query)` runs fixed SDK-owned source and returns at most 100 active non-share users excluding `SUPERUSER_ID`, with only ID, login, and display name. An empty or bounded query searches login/name; users can refine it instead of paging.

`create_browser_session_command(user_id)` rechecks the user and the single resolved database, creates a non-`su` Odoo 19 filesystem session, and is always mutating because the session store is outside the rolled-back database transaction. The child writes SID only to an SDK-owned 0700 directory/0600 exclusive file; the parent reads it once and removes the directory in `finally`. No public model or ordinary command projection contains SID.

Alternative: implement Odoo scripts and subprocesses in HTTP. Rejected because the existing SDK already owns runtime resolution and immutable process plans.

### D2: A minimal one-time handoff bridges panel and instance hosts

The same-origin/CSRF-protected panel exposes `POST /api/v1/odoo/users/query` and `POST /api/v1/odoo/login-tickets`. Issuance stores a cryptographically random token in a process-local locked dictionary for 30 seconds with only owner, current runtime/database/publication host, selected user, and panel origin. It creates no Odoo session.

`POST /_odcli/auth/consume` accepts exactly one form field, verifies the trusted proxy boundary, bound instance host and panel Origin, then atomically pops the token. It re-resolves the same owner/runtime/database and user before invoking session creation. Success sets `session_id` with `Path=/; Secure; HttpOnly; SameSite=Lax`, no `Domain`, and returns 303 to fixed `/odoo`. Any consume attempt is one-shot. Expired entries are removed lazily; restart invalidates the map. The shipped monitor is already one process, so no new worker-mode policy or distributed store is added.

Alternative: return SID or put it in a URL. Rejected because it creates a reusable browser credential in ordinary projections/history.

### D3: Publication reuses the existing panel route endpoint

When rendering the one owned route file, the renderer finds the existing panel route's `local_endpoint` and adds an exact POST-only consume branch before each Odoo proxy. No new endpoint field is copied into every instance route. If no live panel route exists, the consume branch is absent and the UI reports the operation unavailable. All other instance paths still reach Odoo and retain current Basic Auth/proxy behavior.

### D4: React remains a small transient workflow

**Open as...** opens a dialog, performs a bounded user query, lets the operator refine the search and choose one user, then synchronously opens a blank tab, requests a handoff, and submits a hidden form to the returned action URL. The form/input and local token variable are removed immediately. The existing **Open Odoo** action is unchanged. The dialog states that tabs on one instance host share a cookie.

### D5: Verification proves only irreducible boundaries

Focused tests cover filtering, session construction/private cleanup, one-shot expiry/replay, proxy/Origin/host checks, exact Caddy routing, OpenAPI/codegen, and UI cleanup. The existing full real-Odoo job gains one source-backed session test. An independent numbered-slot walkthrough uses one small fixture helper, captures equivalent panel before/after UI, opens one deliberately selected non-superuser, checks safe UID/database session info, verifies a second instance host remains unauthenticated, logs out, and cleans the slot. Replay and malformed-boundary cases remain deterministic tests because live repetitions add no new evidence.

## Risks / Trade-offs

- **Odoo session internals differ** → support only the inspected Odoo 19 filesystem store and fail with one sanitized unsupported response.
- **One-time map is process-local** → acceptable for the existing single-process monitor; distributed workers remain out of scope.
- **Session created before response acknowledgement may remain** → clean known failures best-effort and document the irreducible disconnect window.
- **One host cookie affects all tabs for that host** → disclose it in the dialog; per-tab isolation remains out of scope.
- **Direct server impersonation bypasses password/MFA challenge** → restrict it to the already-protected operator panel and never claim password/MFA authentication occurred.

## Migration / Rollback

No data migration is required. Starting the external monitor reconciles the owned route file with the consume branch. Rollback removes the UI/routes and restarts the monitor, invalidating pending handoffs; normal Odoo sessions retain their ordinary expiry/logout behavior.

## Open Questions

None. Unsupported Odoo/session layouts fail closed rather than selecting another mechanism.
