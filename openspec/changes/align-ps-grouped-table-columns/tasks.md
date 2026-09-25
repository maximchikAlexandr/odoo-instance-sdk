## 1. Frame alignment characterization

- [ ] 1.1 Add a process-inventory fixture whose shared, main-checkout, and environment sections have deliberately unequal `Type`, `PID / scope`, and multiline `Details` lengths.
- [ ] 1.2 Add parameterized 80/120/180-column Rich-output assertions that locate every process table and prove identical outer widths and vertical-boundary indices while retaining every row and typed fact.
- [ ] 1.3 Add a single-section regression proving layout coordination does not add a placeholder section or a second table.

## 2. Shared process-frame layout

- [ ] 2.1 Refactor `commands/ps.py` to project ordered private section values containing the existing title, row tuples, and optional storage text before any Rich table is constructed.
- [ ] 2.2 Implement the private seven-column allocator using Rich display-cell measurement over all sanitized headers and cell lines, including deterministic fitting to the supplied positive console width and supported-width header preservation.
- [ ] 2.3 Apply the resulting explicit integer widths to every section's `bordered_table` while preserving `overflow="fold"`, existing styles, section order, storage facts, empty rows, and pure rendering.
- [ ] 2.4 Cover Unicode, multiline cells, constrained wrapping, and line bounds without adding a generic grouped-table API or changing `commands/output.py`.

## 3. One-shot and live command wiring

- [ ] 3.1 Make one-shot Rich rendering pass the active console width through `_render_ps_rich` and add a public `odcli ps` CLI regression that proves aligned emitted borders for unequal sections.
- [ ] 3.2 Make every successful `odcli ps --watch` refresh read the current console width before rendering, without persisting a width plan beside the last-good frame.
- [ ] 3.3 Add a synchronous fake-`Live` watch regression with successive unequal inventories and changed widths, proving that each captured frame is internally aligned and the later frame is recalculated.
- [ ] 3.4 Preserve first-failure, retry-marker, last-good-frame, sleep, interrupt, and exit-code behavior in the existing watch tests.

## 4. Compatibility and delivery evidence

- [ ] 4.1 Run focused process-presentation, public CLI, output-boundary, delivery-compatibility, and JSON/TOON parity tests; repair only regressions caused by this change.
- [ ] 4.2 Run Ruff and strict mypy for the touched production and test paths, then run the repository-required PR validation gate and record any external-prerequisite skips separately.
- [ ] 4.3 Re-read every `process-inventory` delta scenario against the final diff and record evidence that no collector, typed model, machine envelope, dependency, persisted schema, or unrelated renderer changed.
