## Why

Operators can open a published Odoo instance, but they cannot select an eligible Odoo user and enter that instance without knowing or changing the user's password. The platform already owns the runtime, publication, SDK command, and protected dashboard boundaries needed to provide that workflow without installing an Odoo module, so the missing capability can be added without creating a second authentication platform.

## What Changes

- Add typed SDK operations that list eligible Odoo users and create an Odoo browser session through the selected runtime, with an inspectable command sibling and a secret-safe result boundary.
- Add protected dashboard operations for querying users and issuing a short-lived, single-use login ticket.
- Return the ticket only in the authenticated, `Cache-Control: no-store` issuance response for immediate transient form submission; forbid it from every other JSON/result/log/audit/evidence projection and from URLs or storage.
- Add an instance-host `POST /_odcli/auth/consume` transport that validates the proxy, host, origin, ticket, and current runtime before setting a host-only Odoo session cookie and redirecting to `/odoo`.
- Extend the owned Caddy route so only the exact consume path reaches the dashboard backend while all other instance traffic continues to reach Odoo.
- Add an **Open as...** dialog beside the existing Open Odoo action, including search, keyset pagination, popup-safe POST form submission, and credential cleanup.
- Add focused unit/contract/CI coverage and isolated real-browser verification for session identity, ticket replay/concurrency, trust boundaries, logout, MFA coexistence, secret projections, and cleanup.
- Preserve the existing Open Odoo action, Odoo source/database/module state, publication Basic Auth policy, and ordinary Odoo routes.

## Capabilities

### New Capabilities

- `odoo-browser-session-login`: Eligible-user discovery, one-time login tickets, secret-safe Odoo session creation, browser handoff, UI behavior, audit, and failure semantics.

### Modified Capabilities

- `command-execution`: Classify browser-session creation as a mutating command even with a rolled-back database transaction and require a closed secret handoff.
- `dashboard-http-api`: Publish the new typed panel and instance-host operations without weakening the existing dashboard boundary.
- `instance-publication`: Route only the exact instance-host consume operation to the dashboard backend while preserving all other publication behavior.
- `web-sdk-codegen`: Generate and consume the new panel request/response/error contracts.
- `real-odoo-e2e-verification`: Verify the browser session and proxy flow against an isolated real Odoo runtime without persisting credentials in evidence.

## Impact

- Public SDK models and `OdooInstance` methods; fixed Odoo-shell scripts and private temporary-secret handling.
- FastAPI models, OpenAPI schema, runtime controller, middleware/trust checks, audit, bounded worker execution, and an in-memory ticket store.
- Caddy publication route models/rendering and their compatibility tests.
- Generated TypeScript client, React dashboard components, and frontend tests/build artifacts.
- Unit, integration, security/redaction, OpenAPI/codegen, packaging, CI, and isolated Mac browser E2E coverage.
- No new runtime dependency, database schema, Redis service, CLI command, Odoo addon, or durable bearer credential.
