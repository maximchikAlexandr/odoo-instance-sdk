## Why

`EnvironmentMonitor.processes_command()` currently captures only its outer projection action, while `build_process_inventory()` constructs each `pg_stat_activity` process step after execution has entered that immutable command context. A healthy owned PostgreSQL cluster therefore turns the advertised read-only `ps` path into an `UnplannedStepError` instead of an inventory result.

## What Changes

- Capture every bounded PostgreSQL backend-attribution specification that the selected process-inventory snapshot may consume before constructing the public command, including an inspectable deferred-capability step when `psql` is absent at construction time.
- Include each exact private PostgreSQL step and its redacted public projection in the same immutable `processes_command()` plan, then consume that captured specification at most once through the shared process boundary.
- Preserve typed partial degradation for missing executable/spawn, timeout, privilege, invalid-response, and unavailable-transport outcomes without failing unrelated inventory rows.
- Add public SDK/CLI-boundary regression coverage for successful, non-zero, and construction-plus-execution missing-`psql` attribution while preserving one canonical snapshot across Rich, JSON, and TOON.
- Keep PostgreSQL transport, process-ledger validation, frozen inventory schemas, and public SDK/CLI signatures unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `process-inventory`: require `processes_command()` to preplan the exact bounded `pg_stat_activity` process steps consumed by backend attribution and to degrade only the affected PostgreSQL group when a captured probe cannot produce attribution.

## Impact

- Affected implementation: `EnvironmentMonitor.processes_command()` and its catalogue-backed backend-attribution preparation, the internal process-inventory attribution seam, and focused SDK/CLI-boundary tests.
- Affected contract: the existing `process-inventory` capability gains explicit immutable-plan and partial-degradation scenarios; machine inventory schemas and rendering contracts remain stable.
- Dependencies and storage: no new dependency, database migration, catalogue schema change, or external service is introduced.
