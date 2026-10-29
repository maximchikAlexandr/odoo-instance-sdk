## Why

Coordinated `odcli update` cannot currently update an installed `odcli-multica`: the generated VCS requirement points at the monorepo root instead of the companion package, and verification calls an unsupported `--version` option. A valid two-tool update therefore stops with retained recovery state and requires manual repair even when both installed revisions are correct.

## What Changes

- Generate the companion VCS requirement with the fixed `packages/odcli-multica` subdirectory while continuing to pin the companion and its embedded SDK to the selected immutable revision.
- Verify that the installed companion launcher starts through a supported non-mutating command, and verify both installed VCS revisions from protected package metadata.
- Preserve the current journal, rollback, resume, redaction, and core-only behavior when companion installation or verification fails.
- Add focused command-construction, success/failure verification, recovery, and isolated uv-tool packaging coverage for a coordinated update.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `self-update`: make the existing coordinated companion installation and verification contract executable for the monorepo package layout and the actual companion CLI surface.

## Impact

The change is limited to the existing self-update composition and frozen command construction in `src/odoo_instance_sdk/internal/self_update_composition.py` and `src/odoo_instance_sdk/internal/self_update_commands.py`, their focused unit/packaging tests, and directly affected operator documentation. It adds no dependency, command, storage migration, network provider, or Odoo runtime behavior.
