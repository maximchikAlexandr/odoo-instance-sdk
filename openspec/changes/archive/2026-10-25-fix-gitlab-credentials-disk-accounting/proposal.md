## Why

`odcli-multica` currently bypasses the core encrypted GitLab credential resolver, so bridge push and merge-request operations fail when the verified root creator has only an encrypted-store record. The monitor also presents an incomplete checkout-only `Resources` value instead of a useful disk metric that accounts for related databases and downloaded backups.

## What Changes

- Route credential-dependent `odcli-multica` Git and GitLab operations through the existing encrypted-store-first resolver for the exact verified root creator and HTTPS host.
- Preserve the existing project mapping plus token-environment fallback only when the encrypted store has no matching record; keep explicit path/environment overrides and fail closed on broken, ambiguous, or mismatched records.
- Replace the card detail-row `Resources` presentation with `Disk` alongside CPU and RAM, including stopped runtimes when storage is measurable.
- Expand the project disk aggregate to cover the main checkout/artifacts, working environments, distinct related PostgreSQL databases and filestores, and project-owned downloaded backups without double counting; expose the backup subtotal and incomplete state.
- Add focused credential, storage arithmetic/deduplication, API/codegen, and dashboard rendering regressions. Local E2E is excluded at the user's explicit request because deterministic lower-level checks cover the changed boundaries.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `multica-user-credentials`: make encrypted member/host records authoritative for bridge GitLab credentials while retaining the current project-file fallback only for exact absence.
- `environment-monitor`: define the complete project disk aggregate, backup subtotal, de-duplication and incomplete-value behavior, and place Disk in the instance-card metric row.

## Impact

- Credential resolution: `packages/odcli-multica/src/odcli_multica/client.py` and its credential/GitLab tests, reusing `src/odoo_instance_sdk/internal/gitlab_credentials.py` and the existing secret store.
- Monitor model and collection: `ProjectStorageFootprint`, monitor planning/collection/storage code, backup catalogue reads, generated OpenAPI schema/client, and focused Python tests.
- Dashboard: `src/odoo_instance_sdk/web/src/App.tsx`, styles, and component/contract tests.
- No new dependency, credential format, storage subsystem, database migration, CLI command, or live-environment workflow is introduced.
