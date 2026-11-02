## Why

The panel can open a published Odoo instance but cannot choose an eligible user and enter Odoo without that user's password. The smallest safe solution reuses the existing SDK shell runner, protected FastAPI panel, generated client, and Caddy publication instead of introducing another authentication platform.

## What Changes

- Add one typed SDK query for a bounded list of eligible users and one SDK session-creation command with a private SID handoff.
- Add protected panel operations to query users and issue a short-lived one-time handoff, plus one exact instance-host form endpoint that consumes it, sets the Odoo cookie, and redirects to `/odoo`.
- Extend the existing Caddy renderer with only that exact consume branch, reusing the already-published panel endpoint.
- Add a small **Open as...** dialog beside **Open Odoo** with bounded search and transient form submission.
- Add focused SDK, HTTP, publication, generated-client, React, and real-Odoo checks plus one isolated browser walkthrough.
- Keep passwords, SID, session tokens, and the handoff value out of URLs, logs, retained state, ordinary JSON, Git, and evidence.
- Preserve ordinary Odoo routes, Basic Auth, runtime state, database/module/source state, and the existing **Open Odoo** action.

## Capabilities

### New Capabilities

- `odoo-browser-session-login`: Select an eligible local Odoo user and safely open the published instance with that user's browser session.

### Modified Capabilities

- `command-execution`: Treat filesystem session creation and private SID transfer as an inspectable mutating SDK command.
- `dashboard-http-api`: Add typed protected user-query/handoff operations and one narrowly exposed form-navigation endpoint.
- `instance-publication`: Route only the exact consume POST from an instance host to the existing live panel backend.
- `web-sdk-codegen`: Generate the panel contracts and use them from the React dialog.
- `real-odoo-e2e-verification`: Prove the selected-user session through an isolated real browser/runtime without retaining credentials.

## Impact

- SDK instance commands/models and focused tests.
- FastAPI models/router/controller, OpenAPI, and one in-memory one-time handoff map.
- Existing Caddy route renderer, generated TypeScript client, React card/dialog, and related tests.
- One small test-repository fixture helper, isolated browser evidence, and operator limitations documentation.
- No new dependency, daemon, durable credential store, CLI leaf, Odoo addon, database migration, SSO/RBAC layer, or general-purpose framework.
