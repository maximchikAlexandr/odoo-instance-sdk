## Why

Project-aware commits currently treat a native task checkout without its own `.odcli/project.toml` as generic, so a verified Multica member cannot supply the required author and committer identity. User secrets are also split across project and credential dotenv files, merge-request assignment has no administrative default, and existing encrypted-secret records cannot safely change their identity fields through Admin.

## What Changes

- Accept a registered project/environment binding as authoritative for a native checkout, resolve the exact member/host GitLab identity, and fail before commit when checkout, host, member, or identity evidence is not exact.
- Add one owner-only `<ODCLI_USER_ROOT>/.env`, with process > project > user precedence, for `ODCLI_ADMIN_PASSWORD` and legacy GitLab token keys; retain the old credential dotenv only through an explicit conflict-safe compatibility rule.
- Add a typed, canonical JSON settings table to the existing catalog and a small authenticated Starlette Admin CRUD view. Define an exact host/project-scoped default MR-assignee key whose value is a GitLab numeric user ID or exact username.
- Reuse the existing `odcli-multica gitlab mr publish` path so an explicit `--assignee` overrides the catalog default; validate the selected user and project membership before create/update and verify the returned assignee.
- Let both existing secret Admin views edit every business field while keeping passwords/tokens write-only, metadata non-editable, and identity changes atomic under optimistic locking.
- Extend unit, component, integration, documentation, and focused live Admin verification coverage for the changed contracts.

## Capabilities

### New Capabilities

- `catalog-settings`: Typed catalog-backed scalar/JSON settings, authenticated Admin management, and exact host/project MR-assignee configuration.

### Modified Capabilities

- `odoo-git-workflow`: Project-aware verified-member commits recognize registered native checkouts without a checkout-local manifest.
- `gitlab-member-credentials`: Legacy GitLab token fallback moves to the single user dotenv with conflict-safe compatibility for the old path.
- `project-init`: User and project dotenv sources share the existing safe grammar, permission, precedence, propagation, and redaction boundary.
- `database-restore`: Noninteractive administrator password reset also resolves the user dotenv for init/restore/refresh/reset flows.
- `gitlab-merge-request`: MR publication resolves, validates, applies, and verifies a configured default assignee while preserving explicit override behavior.
- `secret-store`: Admin can atomically change secret-record identity and business fields without exposing secrets or editable concurrency metadata.
- `cli-odcli`: Help and safe projections document the unified user dotenv, native commit identity behavior, and configured MR assignee outcome.

## Impact

Affected areas include core Git commit planning, project/environment catalog binding, dotenv parsing and secret resolution, catalog schema/migrations, the optional Starlette Admin surface, encrypted-secret compare-and-swap updates, `odcli-multica` MR publication, documentation, and their existing offline/dashboard CI suites. No new dependency, database, UI framework, credential store, or MR creation path is introduced; rewriting MR !705 history is out of scope.
