# Remote sources and reliable local copies

## Why

An agent preparing an Odoo workspace needs deterministic source selection, isolated local copies and bounded backup storage. Project configuration currently exposes one test source and one remote password; repeated operation leaves downloaded backups behind.

## What Changes

- Add named remote sources to the existing project manifest, with SDK/CLI configuration during init and afterwards.
- Resolve source-specific passwords from environment or existing dotenv; trust the selected URL in project.toml.
- Treat origin approval variables as legacy no-ops; remove separate origin-pin checks for named and legacy sources while retaining URL, TLS, redirect, and redaction safeguards.
- Extend refresh and COPY checkout with explicit source selection; retain checkout from an exact catalog backup.
- Preserve source/base provenance, target isolation, neutralization and recoverable failure results.
- Add optional bounded readiness to environment-bound detached launch by reusing the current persisted-runtime and listener-owner proof.
- Add backup pinning, newest-per-source protection, age-based pruning and opt-in post-success cleanup through the existing catalog.
- Extend existing user-level `user.toml` with retention settings, exposed through SDK/CLI.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `project-init`: Named remote configuration and atomic editing.
- `client-config`: User-level backup retention settings.
- `project-database-preparation`: Explicit source and credential selection.
- `development-environment`: COPY checkout from a remote or retained backup.
- `server-lifecycle`: Opt-in detached readiness.
- `backup-catalog`: Pinning and safe retention.
- `cli-odcli`: Thin CLI projections of these public primitives.

## Impact

Extend existing configuration, preparation/checkout commands, backup facade, linear Alembic catalog migrations and canonical CLI inventory. Preserve legacy single-source behavior and disabled automatic retention. Reuse the current HTTP transport, immutable command model, lifecycle locks, exact runtime/listener inspection, catalog deletion and recovery paths; no new runtime dependency is expected.

The package supplies technical primitives. Access roles, approval/anonymization of data, application workflows and reports belong to callers. Remote profiles are backup sources, not deployment targets.
