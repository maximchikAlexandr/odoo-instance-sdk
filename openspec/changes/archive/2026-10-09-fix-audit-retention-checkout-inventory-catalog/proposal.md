## Why

The MYL-421 audit found four boundary defects in otherwise established SDK contracts: retention updates can absorb an array-of-tables into `[backup]`, adopted-checkout plans can lose their captured base revision, large provider results can deadlock behind the parent’s `join`, and catalogue compatibility checks encode the same historical schema in divergent branches. These defects can corrupt configuration meaning, publish incomplete provenance, silently drop optional inventory facts, or reject/repair legacy catalogues inconsistently.

## What Changes

- Make retention-policy patching stop at every TOML table header, including `[[array-of-tables]]`, while preserving all unrelated content and proving the written file with `tomllib`.
- Route ordinary checkout and adoption through one internal provenance normalization and public/execution projection path, retaining adoption-specific ownership validation and publishing the captured resolved base revision.
- Drain an environment-facts provider channel while its child process runs under one overall deadline, retaining bounded termination and accepting large valid payloads.
- Define historical catalogue shapes once as version-explicit internal fingerprints reused by v16 repair and legacy-provenance recognition, while preserving the Alembic verification/stamping boundary.
- Add focused regressions for the actual TOML structure, public adoption plan, large provider payload, and both historical-catalogue recognition paths.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `client-config`: Clarify that retention updates preserve following array-of-tables and remain parseable with values owned by `[backup]` only.
- `development-environment`: Require adoption to publish the captured resolved base revision through the same normalized plan projections as ordinary checkout.
- `cli-odcli`: Require bounded provider collection to accept valid responses larger than a pipe buffer without deadlock or silent loss.
- `backup-catalog`: Require shared, version-explicit historical schema fingerprints for v16 repair and legacy-provenance recognition before Alembic stamping/upgrading.

## Impact

- Affected internals: `internal/backup_retention.py`, `resources/environment/checkout.py`, `checkout_api.py`, `checkout_stages.py`, `internal/checkout_inventory.py`, and `storage/catalog_migrate.py`.
- Affected tests/fixtures: retention resource tests, adoption plan tests, checkout-inventory provider tests, and catalogue migration fixtures/tests.
- Public method names, result types, CLI syntax, persisted catalogue head, dependencies, and user-data ownership rules remain unchanged.
