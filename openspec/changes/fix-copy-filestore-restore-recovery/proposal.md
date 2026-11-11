## Why

COPY checkout can complete the PostgreSQL import but leave its journal at `restore_pending` when the restore server, generated environment config, and attachment verifier disagree about the target `data_dir`. The resulting `cleanup_failed` environment cannot be removed through the public lifecycle, and a blob already missing from the source archive is incorrectly reported as new target data loss.

## What Changes

- Give every ordinary and adopted COPY checkout one immutable environment-owned target `data_dir` used by the generated config, auxiliary restore runtime, restore provenance, attachment verification, and cleanup.
- Materialize and validate the selected archive's filestore baseline under the exact target database root without following symlinks or changing the source archive/filestore.
- Compare restored attachment references with that captured source baseline: report source-missing blobs as warnings, while failing closed when a blob present in the baseline is absent or unsafe in the target.
- Promote the COPY journal after a proven complete database import so a later filestore/postcondition failure can use the existing guarded, idempotent `env rm` path after catalog reopen.
- Preserve exact database, backup, cluster, path-containment, borrowed-backup, unrelated-resource, and caller-owned-checkout protections.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `development-environment`: align COPY filestore identity and baseline verification, and make a proven post-import failure recoverable through the existing environment lifecycle.

## Impact

Affected areas are environment checkout planning/execution, validated backup filestore materialization, COPY journal transitions, guarded environment removal, environment warnings/audit output, focused unit/catalog-reopen regressions, and the existing real-Odoo verification path. No new command, dependency, migration, or workflow engine is introduced.
