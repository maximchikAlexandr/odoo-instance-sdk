## Why

`odcli ps` and `odcli ps --watch` render each process section as an independent Rich table, so Rich chooses different widths for the same seven columns when section contents differ. The resulting borders do not align within one inventory frame even though the tables are projections of one common process dataset.

## What Changes

- Coordinate the seven process-table column widths once per Rich inventory frame across shared resources, the main checkout, and environment sections.
- Size each named column for the widest header or cell in the whole frame, then fit the shared grid to the available terminal width without dropping rows, sections, or typed facts.
- Recompute the grid for every live refresh so changing process values and terminal width produce one internally consistent frame.
- Preserve independent section headings and tables, existing section order, styles, row contents, storage facts, wrapping, and one-table behavior.
- Add public-command regression coverage for one-shot and live rendering with unequal cell lengths at a fixed terminal width.
- Keep JSON/TOON documents, `ProcessInventory`, collection, ownership, lifecycle, and process data unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `process-inventory`: Require every process table rendered in the same Rich frame to use one width allocation for the common named columns in both one-shot and watch modes.

## Impact

- Affected implementation: the pure Rich projection in `src/odoo_instance_sdk/commands/ps.py`; the shared `bordered_table` geometry remains the table-construction boundary.
- Affected tests: focused process-presentation tests plus public `odcli ps` one-shot/live CLI regressions at fixed console widths.
- Public compatibility: no command, option, model, machine-envelope, dependency, collector, or persisted-schema change.
- Interaction with the active `refactor-cli-output-boundary` change: no overlap in its stated capability scope or planned files beyond consuming the already-established Rich output helpers.
