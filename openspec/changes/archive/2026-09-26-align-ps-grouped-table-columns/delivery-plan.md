## Planning baseline

- Task: `MYL-281`; GitHub source: `maximchikAlexandr/odoo-instance-sdk#98`.
- Approved base: `origin/main` at `3d688b26b463d273e80fa46500226158d7d9fab1`.
- OpenSpec change: `align-ps-grouped-table-columns`.
- Authoritative estimate totals are stored only in the planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`. The verified headline property selects the required `single_wp_no_dag` topology.
- Estimate basis: remaining implementation to all acceptance scenarios by one experienced developer familiar with Python, Click, Rich, msgspec, and this repository, without AI acceleration.
- Confidence is medium and calibration is unavailable. The render path, tests, local analogues, and compatibility boundary are inspectable; the main bounded uncertainty is Rich display-width allocation across Unicode, multiline content, narrow terminals, and deterministic live-loop fakes.
- Evidence: `commands/ps.py` already owns pure row adapters and separate section tables; `_render_ps_rich` accepts but currently does not consume a width; every section independently invokes `_process_table`; `bordered_table` fixes common border/padding geometry; focused 80/120/180 presentation tests and public CLI/live patterns are available for reuse.
- Estimation did not run tests or application code. It inspected the complete OpenSpec package, current source/tests/configuration, relevant local history, and the source snapshot with only this planning change uncommitted.

## Delivery topology

Mode: `single_wp_no_dag`.

Exactly one implementation package covers the complete task list. Operational ownership stays with one Implementer/WP Verifier pair, avoiding artificial coordination for a localized renderer and its directly coupled public-command evidence.

## WP-01 — Align grouped process-table columns

- **Task coverage:** `1.1`–`1.3`, `2.1`–`2.4`, `3.1`–`3.4`, and `4.1`–`4.3`, each covered exactly once.
- **Deliverable:** one verified `ps` Rich projection that derives a single constrained seven-column layout from all process sections in each frame and applies it to every separate table in one-shot and live modes.
- **Owned responsibility scope:** `src/odoo_instance_sdk/commands/ps.py`; focused process-presentation and public `odcli ps` one-shot/live tests; directly related fixtures, compatibility tests, and evidence notes needed to complete the listed tasks. `commands/output.py`, typed inventory models, collectors, machine serializers, dependencies, and persisted schemas remain read-only unless the approved OpenSpec is revised.
- **Contract surface:** private ordered section projection; private deterministic seven-width allocator based on Rich display-cell measurement and existing `bordered_table` geometry; `_render_ps_rich` consumes the supplied frame width; one-shot uses the active console width; every successful live refresh reads the current console width; existing seven headers, rows, section order, styles, storage facts, sanitization, folding, retry behavior, and JSON/TOON results remain unchanged.
- **Definition of done / evidence:** rendered 80/120/180 frames prove matching outer widths and same-named boundary indices across unequal sections; single-section, Unicode, multiline, empty, stopped, last-good retry, width-change, row/fact preservation, no-collection, and machine-parity regressions pass; focused suites, Ruff, strict mypy, and the repository PR gate complete with external-prerequisite skips reported separately; final diff maps every delta scenario to evidence.
- **Execution-safety rationale:** production behavior is localized to one renderer and its tests, while live and one-shot verification share the same private layout contract. Splitting them would create overlapping writes and a contract handoff without an independent deliverable.

## Coverage proof

WP-01 is the sole execution unit and covers every checkbox in `tasks.md`. No OpenSpec task is omitted or assigned more than once.
