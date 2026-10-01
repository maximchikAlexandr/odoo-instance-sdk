## Why

Restore planning currently passes the configured `data_dir` directly to `shutil.disk_usage()`. When that destination does not exist yet, the inspection error is converted into fabricated zero capacity, so a valid dry-run is rejected as “insufficient local disk space” even though no capacity measurement occurred.

## What Changes

- Measure restore capacity on the configured destination when it exists, or on its nearest existing directory ancestor when the destination is not yet present.
- Keep restore planning side-effect free: capacity inspection SHALL NOT create the configured `data_dir` or any missing parent.
- Preserve the existing reserve policy and insufficient-capacity rejection when free space is measured successfully.
- Report path or filesystem inspection failures as actionable inspection diagnostics rather than as measured zero capacity.
- Add focused shared-preflight tests and a public restore-command regression covering missing and existing `data_dir` paths, genuine insufficient capacity, inspection errors, and absence of directory creation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-restore`: Define truthful, side-effect-free filesystem selection and failure semantics for restore disk preflight.

## Impact

- Affected implementation: `src/odoo_instance_sdk/internal/backup_validation.py` and the existing restore planning call chain that consumes its typed failures.
- Affected verification: focused backup-validation tests plus public CLI/SDK restore command-construction coverage that requires neither PostgreSQL nor a running Odoo service.
- Public restore behavior changes only for absent or uninspectable destination paths; existing archive safety checks, reserve calculation, immutable command capture, dry-run behavior, locks, database ownership, and genuine insufficient-space rejection remain intact.
- No new dependency, configuration switch, storage abstraction, directory creation, database migration, or catalog change is introduced.
