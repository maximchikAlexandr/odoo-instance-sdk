## Planning baseline

- Task: `MYL-242`; GitHub source: `maximchikAlexandr/odoo-instance-sdk#81`.
- Approved base: `origin/main` at `9a862fbe8ff83d57c452144badbd178327d5e598`.
- OpenSpec change: `unify-bordered-human-tables-process-inventory`.
- Authoritative estimate totals are stored only in the planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; their verified `Estimate, hours` value is above the single-WP threshold, so topology mode is `multi_wp_dag`.
- Estimate basis: remaining implementation to all acceptance scenarios by one experienced developer familiar with Python, Click, Rich, msgspec, and this repository, without AI acceleration. Confidence is medium and calibration is unavailable: the scope and local analogues are inspectable, but the breadth of command fixtures, live terminal wrapping, and downstream gate repairs remains coupled.
- Evidence: 32 direct Rich table constructions across 11 command modules; explicit borderless `ps` and wide `env list`; text-block narrow `env list`; five named structured-prose families; stdout-bound recording consoles in command projections; existing `ProcessInventory`, `ClusterSnapshot`, CLI envelope, and presentation test fixtures available for reuse.
- Estimation did not run tests or application code. It inspected OpenSpec artifacts, current source/tests/configuration, relevant local history, and the clean source snapshot apart from this planning change.

## Topology

```text
WP-01 presentation foundation
  ├── WP-02 data command tables ──────────────┐
  ├── WP-03 workflow tables and prose ────────┤
  ├── WP-04 environment tables ───────────────┼── WP-06 compatibility and delivery gate
  └── WP-05 process inventory table ──────────┘
```

Stage 1 establishes and freezes the shared presentation contract. Stage 2 is a real parallel frontier of four independent command-area packages with non-overlapping production write zones. Stage 3 starts only after every sibling contract is delivered and owns cross-command evidence and any integration repair.

## WP-01 — Presentation foundation

- **Task coverage:** `1.1`, `1.2`, `1.3`, `1.4`.
- **Deliverable:** one minimal, tested presentation boundary providing `bordered_table`, `render_rich_text`, and `postgres_state_cells`, with a source guard against bypassing it.
- **depends_on:** none.
- **Stage / topological level:** 1.
- **Owned responsibility scope:** shared Rich table geometry, in-memory serialization, canonical PostgreSQL state/reason projection, and architectural/purity tests. Critical shared files are `src/odoo_instance_sdk/commands/output.py` and the focused output-contract test module(s). Directly related fixtures and documentation are included.
- **Contract surface:**
  - `bordered_table(*headers: str, title: str | None = None) -> Table` uses `box.SQUARE`, row lines, bold header, and safe-box behavior;
  - `render_rich_text(renderable: RenderableType, *, width: int = 180) -> str` has no stdout/stderr side effect;
  - `postgres_state_cells(state: PostgresClusterState, *reasons: str | None) -> tuple[str, str]` returns canonical lifecycle state plus stable de-duplicated details;
  - no command/result registry, public SDK model, dependency, collector, or serializer change.
- **DoD / evidence:** focused unit tests prove border geometry, 80-column wrapping, pure rendering, state/reason separation, and one emission; source guard identifies every remaining direct `Table(...)` call for downstream migration; Ruff and mypy pass for touched files.
- **Parallel-safety rationale:** this package exclusively owns the shared helper API. All later packages depend on its frozen signatures and import them without editing the helper.

## WP-02 — Backup, database, and PostgreSQL tables

- **Task coverage:** `2.1`, `2.2`, `2.3`, `2.4`.
- **Deliverable:** all structured Rich tables in backup/database/PostgreSQL command families use the foundation geometry and consistent cluster state wording, with focused width and unavailable-data coverage.
- **depends_on:** `WP-01`.
- **Stage / topological level:** 2.
- **Owned responsibility scope:** `commands/backup.py`, `commands/db.py`, `commands/pg.py` and their directly related unit tests/fixtures. It owns no environment, process-inventory, module/workflow, or shared-helper edits.
- **Contract surface:** existing typed results and machine envelopes are unchanged; `db stats`/`db bloat` keep separate data/index tables; confirmation, exit, diagnostics, and unavailable-reason semantics remain intact; `postgres status` imports `postgres_state_cells` for its lifecycle/details cells.
- **DoD / evidence:** command-local tests cover visible borders and separators, supported widths, empty/long/unavailable values, preserved rows/fields, state/reason separation, and unchanged JSON/TOON semantics; focused Ruff/mypy/tests pass.
- **Parallel-safety rationale:** production writes are limited to backup/database/PostgreSQL command modules, disjoint from the other Stage 2 packages; the shared foundation is read-only.

## WP-03 — Developer workflow tables and structured prose

- **Task coverage:** `3.1`, `3.2`, `3.3`, `3.4`, `3.5`, `3.6`.
- **Deliverable:** developer-workflow tables and the named prose views are bordered, side-effect-free local projections; repeated records are row-oriented and `module ls` emits once.
- **depends_on:** `WP-01`.
- **Stage / topological level:** 2.
- **Owned responsibility scope:** `commands/module.py`, `commands/resource.py`, `commands/git.py`, `commands/translations.py`, `commands/test.py`, `commands/multi_target.py`, `commands/cli_parts/callbacks.py` and directly related tests/fixtures, including VS Code, doctor, detached run, and dependency verification coverage. It does not edit env or ps renderers.
- **Contract surface:** module install order is `Order | Module`; doctor is one `Check | Status | Details` table per applicable scope; detached run and deps verify retain all named facts; multi-target tables appear only for repeated structured records; native foreground/scalar paths remain unchanged; every string projection uses `render_rich_text` and never writes directly.
- **DoD / evidence:** each audited renderer has command-local border/wrapping/empty-state tests; `module ls bcrm` proves one header and one result row; doctor failures and deps failures retain exits/details; detached foreground transport regression passes; focused Ruff/mypy/tests pass.
- **Parallel-safety rationale:** this package owns the workflow/callback write zone as one unit, avoiding sibling conflicts in `module.py` and `callbacks.py`; other Stage 2 packages touch separate modules.

## WP-04 — Environment tables at all widths

- **Task coverage:** `4.1`, `4.2`, `4.3`, `4.4`.
- **Deliverable:** `env list` is bordered at narrow, medium, and expanded widths, and `env show` is a complete fact table with consistent PostgreSQL state/details.
- **depends_on:** `WP-01`.
- **Stage / topological level:** 2.
- **Owned responsibility scope:** `commands/env/checkout.py`, `commands/env/display.py` and directly related environment presentation tests/fixtures. Collection, catalog, public monitor models, and machine-field code are outside its production write scope.
- **Contract surface:** 80-column layout retains name/state and folds branch/database/Git/provider/path facts into `Details`; 120/180 layouts retain their useful independent columns; ordering, styles, human-only path shortening, removed/stopped semantics, and machine values stay stable; state/reason cells come from `postgres_state_cells`.
- **DoD / evidence:** parameterized 80/120/180 tests cover long values, provider facts, empty inventory, removed/stopped rows, unavailable cluster data, border characters, and line bounds; collection spies remain zero during rendering; existing JSON/TOON env tests pass.
- **Parallel-safety rationale:** the environment package has an isolated production/test write zone and only imports the read-only Stage 1 contract.

## WP-05 — Unified process inventory table

- **Task coverage:** `5.1`, `5.2`, `5.3`.
- **Deliverable:** every shared/main/environment `ps` section contains exactly one bordered table with the seven specified primary columns and complete typed rows for Odoo, owned PostgreSQL, backend groups, and external contributions.
- **depends_on:** `WP-01`.
- **Stage / topological level:** 2.
- **Owned responsibility scope:** `commands/ps.py` and directly related process/presentation tests and fixtures. `ProcessInventory`, collectors, monitor resources, public models, and machine serialization are read-only inputs.
- **Contract surface:** pure row adapters map only frozen inventory values; shared ownership/deduplication remains unchanged; host/Docker-VM/unavailable PID scopes remain explicit; product facts move to `Details`; storage remains a non-process section fact; every section has one table including explicit stopped/empty state.
- **DoD / evidence:** tests cover 80/120/180 widths, mixed row kinds, unique/shared backend attribution, Docker-VM missing metrics, unavailable reasons, stopped/empty owners, deterministic section order, one shared occurrence, and no data collection from render functions; machine projection equality remains green.
- **Parallel-safety rationale:** only `ps.py` and process presentation tests are writable, disjoint from environment and other command renderers; shared helpers/models are consumed read-only.

## WP-06 — Cross-command compatibility and delivery gate

- **Task coverage:** `6.1`, `6.2`, `6.3`, `6.4`, `6.5`, `6.6`.
- **Deliverable:** one integrated evidence set proving the full visual contract, state consistency, transport compatibility, and repository quality gates across every prior package.
- **depends_on:** `WP-02`, `WP-03`, `WP-04`, `WP-05`.
- **Stage / topological level:** 3.
- **Owned responsibility scope:** cross-command presentation/architecture/output-mode tests, test fixtures, documentation required by the final contract, and integration repairs in predecessor-owned files after all predecessors have completed. Critical shared tests include CLI rich presentation, output modes, architecture inventory, process inventory, and command-specific regression suites.
- **Contract surface:** identical frozen cluster input yields identical lifecycle labels/details across `ps`, `env list`, `env show`, `doctor`, and `postgres status`; JSON/TOON envelopes and native/scalar transports remain byte/semantic compatible; all direct table construction is routed through the foundation; no public model/dependency/persisted schema or renderer framework appears.
- **DoD / evidence:** cross-command 80/120/180 matrix is green; machine parity, stderr/exit/prompt/operation-count, native stream, and scalar path tests are green; focused suites, Ruff, mypy, full non-external gate, and `make pr` complete with external prerequisite skips reported separately; final diff maps every GitHub #81 acceptance criterion and every OpenSpec scenario to evidence.
- **Parallel-safety rationale:** this join package starts only after all parallel siblings finish, so it can repair any integrated behavior without concurrent write-zone conflicts.

## Task coverage proof

| Work package | OpenSpec tasks covered exactly once |
| --- | --- |
| WP-01 | 1.1–1.4 |
| WP-02 | 2.1–2.4 |
| WP-03 | 3.1–3.6 |
| WP-04 | 4.1–4.4 |
| WP-05 | 5.1–5.3 |
| WP-06 | 6.1–6.6 |

The Stage 2 frontier contains four simultaneously executable packages, so the graph has real parallelism and does not collapse into a linear chain. WIP limits are operational and are not encoded as additional dependencies.
