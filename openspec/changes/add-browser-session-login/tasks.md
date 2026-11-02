## 1. SDK contracts and closed session handoff

- [ ] 1.1 Add immutable browser-login user/page/grant models and public exports with safe repr/JSON/public projections and no dashboard dependency.
- [ ] 1.2 Add fixed-source `list_browser_login_users_command()` plus delegating convenience method with validation, single-database resolution, eligible-user filtering, search, keyset pagination, and timeout handling.
- [ ] 1.3 Add fixed-source `create_browser_session_command()` plus delegating convenience method with runtime/session-store compatibility checks, eligibility recheck, non-`su` Odoo 19 session/token creation, honest mutating actions, and no database commit.
- [ ] 1.4 Implement exclusive owner-only temporary SID handoff, no-follow validation, unconditional cleanup, and bounded best-effort orphan-session deletion without exposing credentials in any projection.
- [ ] 1.5 Add focused SDK tests covering command immutability/delegation, validation, pagination, eligibility, source/argv separation, session construction, unsupported extensions, action honesty, dry-run inertia, temp-file attacks, timeout/cleanup, and exhaustive secret projection checks.

## 2. Dashboard tickets, trust boundary, and audit

- [ ] 2.1 Add canonical typed user-query, ticket, consume, response, and browser-login `HttpErrorCode` models to the existing msgspec/OpenAPI bridge with unknown-field rejection.
- [ ] 2.2 Extend `RuntimeController` with owner/runtime/database/publication resolution and bounded SDK delegation while preserving catalog thread affinity and avoiding nested command execution.
- [ ] 2.3 Implement a bounded locked single-process ticket store with secure entropy, fixed expiry, capacity cleanup, immutable bindings, atomic terminal consume, restart invalidation, and an explicit multi-worker refusal.
- [ ] 2.4 Add same-origin CSRF-protected user-query and ticket routes with no-store responses and no session creation during ticket issuance.
- [ ] 2.5 Add exact consume form handling with bounded parsing, trusted proxy/host/proto/origin checks, current-binding revalidation, one-shot SDK execution, safe cookie/303 headers, 405 behavior, deadline/capacity handling, and failure cleanup.
- [ ] 2.6 Add sanitized browser-login audit records using the available verified or honest shared/local principal and excluding all credential/header/body values.
- [ ] 2.7 Add focused ticket concurrency/time/capacity, HTTP boundary/error, worker/deadline, audit/redaction, OpenAPI, optional-import, and controller thread-affinity tests.

## 3. Publication routing

- [ ] 3.1 Extend the typed publication route snapshot with the captured live dashboard endpoint needed only for consume and validate it without broadening accepted hosts or peers.
- [ ] 3.2 Render a mutually exclusive exact consume POST branch before general Odoo proxying while preserving Basic Auth, upstream TLS-proxy semantics, lock, validation, atomic replacement, reload, and rollback.
- [ ] 3.3 Add publication tests for exact path/method routing, dashboard API non-exposure, unavailable monitor behavior, Basic Auth on/off, trusted forwarded headers, deterministic rendering, candidate rollback, and project/environment reconciliation.

## 4. Generated client and React workflow

- [ ] 4.1 Regenerate and commit OpenAPI/TypeScript artifacts for user-query, ticket, error, and pagination contracts; keep consume out of the JSON client.
- [ ] 4.2 Add the Open as dialog beside unchanged Open Odoo for available project/environment publications, with search debounce, pagination, eligible-admin default, explicit confirmation, and shared-cookie disclosure.
- [ ] 4.3 Implement synchronous blank-tab acquisition and transient form POST with duplicate-submit prevention, DOM/memory cleanup, popup/error handling, and no credential URL/storage/console/analytics exposure.
- [ ] 4.4 Add React tests for availability gating, queries/search/pages, selection/default, popup blocked/success/failure, exact form fields/action, cleanup, duplicate prevention, sanitized errors, existing Open Odoo regression, and production build/package artifact expectations.

## 5. Deterministic integration and CI coverage

- [ ] 5.1 Add integrated FastAPI/Caddy/SDK fake-process tests for successful consume, replay/concurrency, expiry/restart, identity mismatches, ineligible users, unsupported stores, direct-backend refusal, Basic Auth coexistence, logout classification, and credential absence.
- [ ] 5.2 Extend source-backed real-Odoo CI fixtures/tests for filesystem session creation, safe session-info, logout, MFA-setting preservation/impersonation audit, compatible/unsupported store behavior, and secret-registry/evidence packaging assertions.
- [ ] 5.3 Run focused SDK/HTTP/publication/frontend tests, `make web-codegen-check`, dashboard build/tests, lint, types, packaging/smoke, and applicable real-Odoo CI on the exact published revision; keep unattended local suites within the workspace limit.
- [ ] 5.4 Review all public plans/results/events/recordings/logs/audit/generated artifacts for secret absence and all new process paths for the shared `internal/proc`, argv, `shell=False`, command-sibling, and inert-preview contracts.

## 6. Independent isolated browser verification and documentation

- [ ] 6.1 Add the planned test-repository files `scripts/e2e/open_as_fixture.py`, `scripts/e2e/open_as_probe.py`, and `tests/e2e/fixtures/open_as_users.json` for two eligible users with distinct access rights, deterministic reset, replay/trust probes, and failure-safe cleanup in a fresh numbered slot; keep product CI fixtures owned by the Implementer.
- [ ] 6.2 Independently verify readiness and execute the approved live cases on the exact product SHA through real Caddy/panel/browser/Odoo boundaries; capture equivalent panel UI before/after states, safe session identity, access-right difference, host isolation, replay/concurrency, logout, MFA coexistence, and cleanup assertions. Verify unsupported session extensions through the deterministic/source-backed disposition in task 5.2 rather than an undeclared live fixture.
- [ ] 6.3 Publish the Tester final Multica comment with product/test revision links, MR/commit/file links, commands/exit codes, assertions, safe logs/hashes, required before/after images, cleanup result, and attachment read-back; do not commit diagnostic evidence or credentials.
- [ ] 6.4 Document the operator workflow, trusted-boundary assumptions, single-process limitation, supported Odoo/session-store scope, shared-host cookie behavior, audit semantics, failure codes, and irreducible post-session/pre-response orphan window.
