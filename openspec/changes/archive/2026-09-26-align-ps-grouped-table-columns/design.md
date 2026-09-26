## Context

`_render_ps_rich(inventory, width=...)` currently creates each section eagerly through `_shared_section` or `_checkout_section`. Each call passes only that section's rows to `_process_table`, which creates a fresh `bordered_table`. Rich therefore measures seven identical columns against different row sets and chooses different boundaries. The `width` argument is forwarded by callers and tests but is not used during table construction.

The current path already has the correct boundaries around this defect: row adapters are pure projections of frozen `ProcessInventory`; `bordered_table` owns common geometry; `_print_ps_human` and `_run_ps_live` both call `_render_ps_rich`; JSON/TOON bypass this projection. The fix must remain local to the process presentation and preserve the separate section tables introduced by the archived `unify-bordered-human-tables-process-inventory` change.

## Goals / Non-Goals

**Goals:**

- Calculate one seven-column layout from every process row in a single Rich frame.
- Apply the exact same integer widths to every section table at the supplied console width.
- Recalculate the layout on each live sample using the current terminal width.
- Preserve bounded wrapping, existing section/table order, styles, values, storage facts, empty rows, and pure rendering.
- Prove alignment through the public one-shot and watch command paths using actual Rich text output.

**Non-Goals:**

- Combining sections into one table or moving headings/storage facts into rows.
- Changing `bordered_table` for unrelated commands or creating a renderer/layout framework.
- Changing `ProcessInventory`, collectors, ownership, sampling, machine formats, dependencies, or CLI options.
- Persisting width state between frames or optimizing layout with caches.

## Decisions

### D1: Build section row matrices before constructing tables

Refactor the pure projection into two phases inside `commands/ps.py`:

1. Convert shared, main-checkout, and environment blocks into ordered section values containing the existing title, process-row tuples, and optional storage text.
2. Calculate one layout from the union of those row tuples, then construct each existing section table with that layout.

The section value is private and concrete; it carries only already-projected strings and presentation facts. Existing row adapters remain the sole mapping from typed inventory to cells, and no collection can enter the layout phase.

Alternative considered: create each table first and reconcile the Rich `Table` objects afterward. Rejected because table internals are not a stable data source and the projection already owns the row tuples before construction.

### D2: Use one local deterministic width allocator and explicit column widths

Add one private layout function that accepts `_PROCESS_COLUMNS`, all frame rows, and the positive console width, and returns a seven-item immutable integer width tuple. It measures display-cell widths for headers and sanitized multiline cell values with Rich's public text/measurement primitives, including Unicode width, and calculates the available content budget from the existing `bordered_table` edge, separator, and padding geometry.

The unconstrained width for each column is the largest measured header or cell line across the whole frame. If those widths exceed the content budget, the allocator deterministically removes excess from columns that remain above their minimum wrapped width until the grid fits; at the supported 80/120/180 widths it preserves complete headers, while cell text wraps through the existing `overflow="fold"` policy. The final widths are assigned explicitly to all seven Rich columns in every process table. With identical geometry and fixed widths, corresponding borders are identical regardless of section-local content.

The allocator and width application remain private to `ps.py`; `bordered_table` continues to own shared visual geometry and gains no process-specific arguments.

Alternative considered: assign common ratios or `min_width` values and let every table auto-size independently. Rejected because section-local measurement can still produce different integer widths. A generic grouped-table API in `commands/output.py` is also rejected because no other command currently renders multiple tables that must share a grid.

### D3: Treat width as frame input in both one-shot and live paths

`_render_ps_rich` SHALL consume its existing `width` parameter as the authoritative frame width. `_print_ps_human` supplies `console.width`. On every live iteration, `_run_ps_live` reads `console.width` immediately before rendering the successful sample and supplies that value; no width tuple is stored beside `last_renderable`. A transient collection failure continues to show the last successful renderable plus the existing retry diagnostic without recalculating a data frame that does not exist.

Alternative considered: capture the console width once when entering watch mode. Rejected because terminal resizing is part of the live presentation contract and would leave later frames bounded to stale geometry.

### D4: Verify rendered boundaries, not only `Table.columns`

Focused pure tests will create at least two sections with deliberately unequal values, render at 80, 120, and 180 columns, and parse the header/separator lines to assert identical vertical-border indices for each table. They will also retain the existing line-bound, row-presence, ordering, empty-section, sanitization, and no-collection assertions.

CLI regressions will invoke the registered public `odcli ps` command with a fake monitor and fixed-width console. The one-shot case asserts matching boundaries in emitted Rich output. The watch case supplies successive inventories, captures two `Live.update` frames, changes the effective width/content between them, and proves that each captured frame is internally aligned and that the second frame was recalculated. Existing output-mode tests prove JSON/TOON parity.

Alternative considered: inspect only the explicit width tuple. Rejected because that would not prove Rich's final border geometry or the public command wiring.

## Risks / Trade-offs

- **Unicode or multiline values are measured incorrectly** → Use Rich's public display-width measurement on the same sanitized `Text` content passed to tables, and cover wide Unicode plus multiline details.
- **Table chrome is omitted from the budget** → Keep the budget calculation adjacent to the fixed `bordered_table` geometry and assert every rendered line stays within 80/120/180 columns.
- **Aggressive narrowing makes content hard to scan** → Preserve headers at supported widths, shrink only the excess, and keep Rich folding so no value is truncated or dropped.
- **Live tests become timing-dependent** → Replace sleep and `Live` with synchronous fakes and stop the loop through a deterministic test exception after captured updates.
- **The active output-boundary change alters shared helpers** → Depend only on the current public `bordered_table` behavior; rebase before implementation and rerun focused/output compatibility tests if its surface changes.

## Migration Plan

1. Add frame-level alignment characterization tests that fail against the current independent sizing.
2. Introduce private section projection and shared width allocation in `commands/ps.py`, then apply explicit widths to each process table.
3. Wire live rendering to read console width per successful refresh and add deterministic public-command regressions.
4. Run focused process/output tests, Ruff, mypy, and the repository's required PR gate.

No data or schema migration is required. Rollback is a revert of the presentation change; machine contracts and persisted state are unaffected.

## Open Questions

None. The frame scope, width source, constrained allocation, live recomputation, compatibility boundary, and verification method are fixed by this design.
