## Why

Project initialization and database preparation currently publish success from incomplete lifecycle state. An identical `init` can skip missing Compose/bootstrap work, a failed restore can leave a real but unmanageable database, and a successful default switch can leave the project-owned Odoo config selecting the old database.

## What Changes

- Make identical Compose re-init verify and resume the existing Compose/bootstrap postconditions before reporting success, while retaining a true no-op for a complete project and truthful `dry_run` output.
- Preserve exact, auditable recovery evidence when restore failure occurs after the target database has been created, so the supported retry/drop path can reconcile that target without relaxing protection for unrelated databases.
- Update the project-owned generated Odoo database selection atomically with a successful default switch; never rewrite a user-managed source config.
- Add public CLI/SDK regression coverage across the failure, retry, ownership, configuration, and machine-output boundaries.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: identical Compose re-init is a no-op only when owned cluster and bootstrap postconditions are complete; emitted execution metadata remains truthful.
- `project-database-preparation`: restore failures after target creation retain exact recovery provenance, and successful default switches synchronize the owned generated config.
- `database-management`: supported database removal accepts only exact retained partial-restore evidence while preserving fail-closed ownership checks for unrelated databases.

## Impact

The change affects init CLI dispatch and immutable command execution, Compose/bootstrap verification, database-preparation failure retention, restore/catalog provenance, guarded PostgreSQL drop planning, atomic manifest/generated-config writes, and focused unit/integration tests. Public command names and manifest schema remain compatible; no new dependency, launcher, generic lifecycle framework, or weakened image/cluster/database trust rule is introduced.
