## Why

COPY checkout from a selected catalog backup (and the shared explicit-source execution path used by named remotes) consumes the single planned `database.restore.exists-before` step first through `DatabaseResource.exists()` and then again through the checkout coordinator. `RunContext` correctly rejects that second consumption, so execution fails before restore even though dry-run presents a valid immutable plan.

## What Changes

- Preserve the existing early, non-mutating target-absence preflight used before checkout artifacts are created.
- Make the coordinator-owned `database.restore.exists-before` and `database.restore.exists-after` probes the only execution-time target existence checks for selected-backup and named-remote COPY restore.
- Keep the existing `DatabaseAlreadyExistsError`, restore transport, copy journal, provenance, rollback, lock, redaction, and cleanup contracts unchanged.
- Add public SDK/CLI-boundary regression coverage using the real checkout command and a recording/fake executor, including successful selected-backup restore, exact once-only probe consumption, dry-run/execution plan parity, and existing-target rejection before restore.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `development-environment`: Require COPY checkout to consume each planned restore existence probe exactly once while retaining early preflight rejection and identical dry-run/execution command snapshots for selected catalog backups and named remotes.

## Impact

- Affected implementation: COPY database preparation in `src/odoo_instance_sdk/resources/environment/settings.py`; no new command step, public API, dependency, catalog schema, or execution context.
- Affected verification: focused checkout resource tests and the existing public CLI/SDK command boundary fixtures under `tests/unit/resources/test_environment_checkout.py` and related CLI tests where needed.
- Compatibility: no intentional user-visible interface change; successful explicit-source COPY checkout is restored to its specified behavior, and existing targets continue to fail with the established typed error.
