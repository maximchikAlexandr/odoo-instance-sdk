## 1. Establish the shared presentation boundary

- [ ] 1.1 Add the minimal style-only bordered-table factory in `commands/output.py` with explicit outer/column/row separators, distinct header styling, safe box behavior, and fold-friendly defaults; keep command data and row mapping out of the helper.
- [ ] 1.2 Add the in-memory Rich render-to-text utility and migrate the shared emission path away from stdout-bound recording consoles so command-local projections can remain side-effect free and `emit()` is the single normal bounded emission owner.
- [ ] 1.3 Add a pure PostgreSQL lifecycle-state/detail formatter that retains canonical state while keeping metrics/server/query availability reasons separate, without adding a public model or collecting data.
- [ ] 1.4 Add focused tests for factory geometry, wrapping, render purity, lifecycle/reason formatting, and one-emission behavior, plus a source-contract guard that rejects direct production `Table(...)` construction outside the shared style helper.

## 2. Migrate backup, database, and PostgreSQL tables

- [ ] 2.1 Convert backup list/inspect/validate/delete plan and result tables to the shared bordered geometry, preserving fields, status/exit behavior, confirmations, empty rows, and long-path wrapping.
- [ ] 2.2 Convert database list/diagnostic/lifecycle tables to the shared geometry while preserving all typed fields, separate diagnostics, plans/results, and machine envelopes.
- [ ] 2.3 Convert PostgreSQL status/locks/stats/bloat/monitoring/image-approval tables, apply the shared lifecycle/reason formatter, and preserve separate stats/bloat index tables plus every typed unavailable reason.
- [ ] 2.4 Add or update focused tests for the migrated data-command renderers at supported widths, including empty, unavailable, and long-value cases.

## 3. Migrate developer workflow tables and structured prose

- [ ] 3.1 Convert module list/info/where/deps/update/install-order to bordered tables, preserve deterministic ordering and typed results, and add a regression proving `module ls bcrm` emits its header and row exactly once.
- [ ] 3.2 Convert resource list/doctor, Git workflow, translation export/validation, Odoo test, and VS Code result tables to the shared geometry without changing operation count or result semantics.
- [ ] 3.3 Render `doctor` as one bordered `Check | Status | Details` table per applicable global/project/environment section, applying the lifecycle/reason formatter and preserving facts, remediations, drift, sanitization, and failure exit semantics.
- [ ] 3.4 Render detached `run` as `Field | Value` with PID, endpoint, owner context when present, and log path; render `deps verify` as `Check | Status | Details`; leave foreground/native streams unchanged.
- [ ] 3.5 Convert repeated multi-target plan/result prose to target/outcome/details tables while retaining scalar messages and confirmation behavior where no repeated structured records exist.
- [ ] 3.6 Add or update command-local tests for every migrated workflow renderer, empty and unavailable values, long wrapping, pure projection, one emission, and unchanged exits.

## 4. Keep environment output tabular at every supported width

- [ ] 4.1 Replace narrow `env list` text blocks with a compact bordered row schema that preserves name and state and moves branch, database, Git, provider, and path facts into wrapping `Details`.
- [ ] 4.2 Retain medium and expanded `env list` layouts at 120 and 180 columns using the same factory, preserving deterministic grouping, status styles, human-only path shortening, all existing machine fields, and shared lifecycle/reason wording.
- [ ] 4.3 Convert `env show` to one local `Scope | Field | Value` table containing environment, project, runtime, PostgreSQL state, metrics availability, and typed reasons.
- [ ] 4.4 Extend environment presentation tests across 80/120/180 widths, empty inventories, unavailable cluster data, long values, removed/stopped rows, and no horizontal overflow.

## 5. Unify process inventory tables

- [ ] 5.1 Add pure row adapters for Odoo groups, owned PostgreSQL clusters, backend groups, external contributions, and explicit empty/stopped entries using `Type | State | PID / scope | Processes | CPU | Memory | Details` and the shared lifecycle/reason formatter.
- [ ] 5.2 Refactor `ps` so each existing shared/main/environment section emits exactly one bordered common process table while shared resources remain single-owned and storage facts remain outside process rows.
- [ ] 5.3 Add process presentation tests for 80/120/180 widths, mixed Odoo/PostgreSQL/contribution rows, unique/shared backend attribution, Docker-VM PID scope, stopped/empty owners, unavailable metrics/reasons, deterministic section order, and zero collection during rendering.

## 6. Compatibility and delivery verification

- [ ] 6.1 Add cross-command frozen-input tests proving `healthy + stats_failed` remains lifecycle `healthy` with separate details and `stopped` remains consistently `stopped` in `ps`, `env list`, `env show`, `doctor`, and `postgres status`.
- [ ] 6.2 Add parameterized presentation coverage at 80, 120, and 180 columns for representative audited command families, including empty data, unavailable metrics, long values, and mixed process rows.
- [ ] 6.3 Run JSON/TOON parity and output-mode tests to prove unchanged envelope v1 values, one stdout document, stderr diagnostics, exit codes, prompts, and operation counts.
- [ ] 6.4 Run native transport tests proving foreground `run`, interactive `shell`, `psql`, `logs --follow`, raw `eval`/`exec`, and scalar `env path` remain unwrapped.
- [ ] 6.5 Run focused CLI/process tests, Ruff, mypy, the full non-external test gate, and `make pr`; report Docker/real-Odoo prerequisite skips separately from project regressions.
- [ ] 6.6 Review the final diff against GitHub #81 and the OpenSpec scenarios, confirm no public model/dependency/persisted-schema change or renderer framework was introduced, and retain only scoped production, test, and documentation updates.
