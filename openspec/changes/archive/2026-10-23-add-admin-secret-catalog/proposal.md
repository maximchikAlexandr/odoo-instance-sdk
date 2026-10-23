## Why

OdCLI has no safe operator UI for maintaining remote credentials or inspecting its durable catalogue, and the monitor cannot show how old the backup behind a local database is. Operators currently have to edit environment files and inspect SQLite directly, which is error-prone and can expose secrets or internal paths.

## What Changes

- Add a single-administrator Starlette Admin application under the existing monitor server, using the package authentication provider and signed session middleware with owner-only local configuration.
- Add one encrypted SQLCipher secret store for remote Odoo master passwords and GitLab member credentials, with optimistic concurrency, write-only secret fields, and fail-closed key handling.
- Resolve secrets from SQLCipher first and use the existing environment/`.env` lookup only when no matching encrypted record exists; never fall back after an invalid or unreadable record.
- Add three paginated, filterable, read-only catalogue views for backups/events, incomplete operations/errors, and environment history, with explicit safe projections.
- Add authoritative backup download time and age to each monitor database card when the current database provenance proves a completed catalogue restore; otherwise show an unknown state.
- Extend the optional dashboard/package and focused verification coverage without changing headless mode, remote database discovery, catalogue ownership, or existing legacy credential ordering.

## Capabilities

### New Capabilities

- `secret-store`: owner-only administrator configuration, encrypted secret records, conflict-safe editing, and canonical secret resolution.

### Modified Capabilities

- `dashboard-http-api`: authenticated Admin composition, safe read-only catalogue operations, and unchanged local/headless security boundaries.
- `environment-monitor`: backup download provenance is projected into the existing snapshot and cards.
- `backup-catalog`: bounded read projections expose backups, incomplete operations/errors, and environment events without leaking unsafe fields.

## Impact

The change affects optional dashboard dependencies and packaging, local configuration paths, a new encrypted data file and key file below `ODCLI_USER_ROOT`, remote Odoo and GitLab credential resolvers, FastAPI/Starlette Admin composition, canonical msgspec/OpenAPI contracts, React monitor cards, catalogue read queries, and focused Python/TypeScript/package tests. It adds no user table, secret-bearing CLI option, second operational catalogue, remote database discovery path, export/import action, or catalogue mutation from the Admin views.
