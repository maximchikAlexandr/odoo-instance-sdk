## Why

Projects can download and restore Odoo backups today, but preparing a trustworthy local database still requires several manual, loosely coordinated steps: selecting the configured test source, preserving its Git provenance, choosing a collision-free local target, resetting the administrator through the ORM, and updating checkout defaults. Combining GitHub #20, #24, and #27 behind the resource boundary makes refresh and checkout share one auditable, failure-safe path after the CLI output foundation from MYL-55.

## What Changes

- Add a project-scoped database preparation workflow used by `odcli db refresh` and checkout freshness handling. It downloads from the configured test instance and optionally restores, resets `base.user_admin`, and atomically switches the project default only after all requested steps succeed.
- Add declarative test-instance, checkout-base, and freshness settings to the project manifest while keeping the remote master password exclusively in `ODCLI_TEST_MASTER_PASSWORD`.
- Extend `Backup` and the existing catalog with nullable source Git branch provenance, including an additive migration for legacy rows.
- Add checkout provenance comparison (`matched`, `mismatched`, or `unknown`) from immutable restore/backup audit data independently of archive availability, fail-before-mutation behavior for known mismatches, explicit legacy-unknown handling, and additive secret-free typed plan reporting without changing canonical `checkout() -> DevelopmentEnvironment`.
- Add local, context-aware `odcli db reset-admin-password`, implemented with the existing Odoo shell and ORM rather than SQL or password hashing.
- Preserve downloaded backups, restored databases, restore mappings, and the previous project default when a later requested step fails; do not add retention, scheduling, or new service/provider abstractions.

## Capabilities

### New Capabilities

- `project-database-preparation`: Project-scoped refresh, optional restore/admin reset/default switch, freshness recheck, locking, typed results, and failure retention semantics.

### Modified Capabilities

- `models-types`: Extend the public `Backup` model with nullable source Git branch provenance and define typed preparation, reset, and checkout plan/result models returned directly across adapters.
- `database-backup`: Allow the existing backup operation to persist caller-supplied declarative source Git branch provenance without detecting it from the remote instance.
- `backup-catalog`: Add and migrate the single nullable provenance column in the existing SQLite catalog without duplicating restore data or creating a second store.
- `project-init`: Extend `ProjectConfig` and manifest round-tripping with non-secret test-instance, default-base, and freshness configuration.
- `development-environment`: Make checkout resolve an effective base, enforce backup provenance and freshness before mutation, and consume the project preparation workflow when refresh is required.
- `cli-odcli`: Add the `db refresh` and `db reset-admin-password` adapters, their option constraints, context rules, and foundation-compatible Rich/JSON/TOON output.

## Impact

- Public models/resources: `Backup`, project configuration types, `DatabaseResource.backup()`, `EnvironmentCheckoutOptions`, checkout planning/results, and a public resource entry point backed by one private preparation workflow.
- Storage: one additive migration of the existing `backups` table; restore provenance remains reachable through the existing `restores.backup_id` and environment `backup_id` relationships.
- Adapters/configuration: a focused `commands/db.py` after rebasing the accepted MYL-55 CLI boundary, `.odcli/project.toml`, `ODCLI_TEST_MASTER_PASSWORD`, and checkout output.
- Existing concrete primitives: `DatabaseResource.restore()`, `BackupCatalog`, `PostgresCluster`, project/instance locks, atomic manifest writes, context resolution, and `OdooInstance.run_shell_script()`.
- Verification: migration/backward-compatibility, secret redaction, failure injection, concurrency, dry-run, and disposable local Odoo/PostgreSQL integration coverage.
