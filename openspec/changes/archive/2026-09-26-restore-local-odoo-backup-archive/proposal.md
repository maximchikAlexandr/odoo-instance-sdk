## Why

`odcli db restore` can restore only a retained catalogue backup UUID, so a standard Odoo ZIP already present on disk cannot enter the supported restore workflow. Users need the existing command and SDK boundary to accept that archive without importing or retaining a second copy in the backup catalogue.

## What Changes

- Add `--file PATH` to `odcli db restore` and require exactly one source: the existing positional backup UUID or the new file option.
- Add one public typed local-archive restore source accepted by the existing inspectable environment restore command.
- Capture file identity and validation evidence during command construction, then create and consume a private verified snapshot before any database mutation so path replacement or content changes fail closed.
- Reuse the existing target reservation, Odoo ZIP validation, database/filestore restore, postcondition, optional administrator reset, failure retention, progress, and atomic default-switch stages.
- Record source-neutral restore provenance for local archives without creating a retained backup catalogue entry or persisting the source path.
- Preserve UUID restore behavior, structured output, redaction, confirmation, dry-run, and the canonical public CLI leaf inventory.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-odcli`: extend the existing `db restore` leaf with mutually exclusive UUID and local-file source selection.
- `project-database-preparation`: add a local archive as a third typed source of the one preparation pipeline and define immutable capture, validation, and cleanup.
- `database-restore`: allow a validated local Odoo ZIP to use the established guarded restore stages without a retained catalogue backup.
- `database-management`: retain safe restore ownership/audit evidence when the source has no catalogue backup UUID.

## Impact

- CLI parsing and source-selection tests in `commands/db.py` and the existing public-leaf characterization.
- Public models/exports and `EnvironmentResource.refresh_database_command()` source typing.
- Database preparation source binding, plan construction, archive validation/snapshot cleanup, failure context, and result projection.
- Database restore provenance schema, Alembic migration, catalogue reads/writes, and ownership regression tests.
- Focused unit/integration coverage, CLI/SDK documentation, and the generated real-Odoo command matrix only if its checked projection changes.
- No new runtime dependency, command group, remote URL source, archive conversion, or catalogue retention/import workflow.
