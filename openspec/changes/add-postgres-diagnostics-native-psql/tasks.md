## 1. Prerequisite and Baseline

- [x] 1.1 Verify MYL-68 is merged, fast-forward local `main`, rebase `feat/MYL-70-postgres-diagnostics` onto it, and stop with an issue comment if the shared `Command`/`ProcessStep`, dry-run, output, or inherited-TTY contract is still missing.
- [x] 1.2 Audit the rebased PostgreSQL process launches, CLI callbacks, output projections, models, and startup imports; record the exact shared APIs to reuse and the direct-process/output violations this change must remove.
- [x] 1.3 Run and record the repository's pre-change project gates plus native foreground/TTY and CLI startup guards so the final result has a reproducible baseline.

## 2. Shared PostgreSQL Transport and Models

- [x] 2.1 Create the short `internal/pg/` package and `commands/pg.py`, move only PostgreSQL-specific transport/CLI implementation into them, preserve lazy imports, and prove existing PostgreSQL lifecycle/database tests still pass before adding behavior.
- [x] 2.2 Add the exact frozen SQL, lock, statistics, bloat, closed capability/warning, monitoring-initialization, and `PostgresServerInfo` schemas from the specs; enforce tuple collections, datetime/nullability, non-negative numeric fields, ratio bounds, and export only the intended public SDK types.
- [x] 2.3 Implement one immutable psql specification builder on the shared executor for captured and inherited-TTY modes, including binary resolution, exact argv/stdin, timeout, sanitized environment, removal of ambient `PGOPTIONS` before private SDK timeout injection, private password handling, and redacted public plan/fingerprint.
- [x] 2.4 Implement the closed token grammar and table-driven pre-spawn tests for every protected identity form, every declared zero-value option, split/attached/long-`=` forms of `-c/-f/-F/-L/-o/-P/-R/-T/-v`, missing values, unknown options, positional database/user/URI/keyword strings, and operands after `--`; prove exact argv boundaries.
- [x] 2.5 Migrate the existing database-existence fallback and other PostgreSQL launches in scope to the shared builder/executor, then delete the superseded direct `subprocess` transport path.

## 3. DatabaseResource Command Surface

- [x] 3.1 Implement `psql_command()` and delegating `psql()` on `DatabaseResource`, including bound database identity, owned-cluster readiness action, external reachability behavior, inherited TTY, native signals/streams, and exit code.
- [x] 3.2 Implement `execute_sql_command()` and delegating `execute_sql()` with exact caller SQL, positive finite timeout validation, captured execution, and sanitized `SqlExecutionResult`.
- [x] 3.3 Implement one reusable database/context resolver for diagnostics and psql; cover registered-worktree default, unambiguous project default, explicit same-cluster database, stopped Odoo, and pre-spawn missing/ambiguous errors.

## 4. Bounded Diagnostics

- [x] 4.1 Independently author and package the versioned lock SQL, decode it into typed rows, enforce top/timeout/query-preview bounds and stable ordering, and unit-test real blocker mapping without persistent database objects.
- [x] 4.2 Author the statistics SQL with the specified total-bytes/bytes top order, buffer-byte and ratio denominators, hot `usagecount >= 3` rule, mandatory cumulative warning, and one quiet transaction using `pg_temp` plus dynamic `DO` handling of SQLSTATE `42P01`/`42883`/`42501`; test one final JSON, stdout suppression, null/zero behavior, and cleanup on success/failure.
- [x] 4.3 Author the bloat SQL with the specified dead-tuple estimates, independent candidate/final `NULLS LAST` orders, top-before-threshold selection, exact `pgstattuple`/B-tree `pgstatindex` formulas, mandatory cumulative warning, and the same dynamic session-local error boundary; test all methods, thresholds, mixed optional failures, one final JSON, and no persistent objects.
- [ ] 4.4 Implement `locks()`, `stats()`, and `bloat()` on `DatabaseResource` using the shared transport, one final JSON value per command, frozen typed decoding, server/subprocess timeouts, and no generic row mapper.
- [ ] 4.5 Implement initialization as one quiet transaction with a `pg_temp` outcome table: for each extension check `pg_extension`, then precheck `pg_available_extensions`, use precheck absence as the only `not_available` path without executing `CREATE EXTENSION`, and use one dynamic `DO` exception subtransaction only for available absent entries; test no-CREATE on precheck absence, classification of only `42501` as `privilege_denied`, re-raise/outer rollback for `0A000` and every other post-precheck CREATE error, exact installed/already-present/skipped meanings, partial commit only for classified outcomes, single final JSON, external rejection, and repeated execution.

## 5. PostgreSQL Status Enrichment

- [ ] 5.1 Add one immutable server-summary query template with the exact client-backend state counts, connectable-database predicate, and uptime formula; execute at most once per de-duplicated maintenance candidate under one monotonic total timeout budget and test remaining-budget behavior and no database disclosure.
- [ ] 5.2 Add the `ServerUnavailabilityReason` Literal alias and locale/SQLSTATE-stable classifier; table-test stop rules for missing/tool/auth class `28`/connection class `08`/timeout/query/decode, continuation for `3D000`/`42501`, mixed-failure `privilege_denied` precedence, preserved exit semantics, and external no-Docker behavior.

## 6. CLI and Output Boundary

- [ ] 6.1 Register thin `db locks`, `db stats`, `db bloat`, `db init-monitoring`, root `psql`, and enriched `postgres status` callbacks from `commands/pg.py` without adding host/user/password/selector flags or argv construction in Click callbacks.
- [ ] 6.2 Add adjacent Rich projections for locks and separate table/index diagnostics, and route JSON/TOON through the one shared typed output document; verify decoded JSON/TOON equality, numeric bytes, clean stdout, warnings, errors, and exit codes.
- [ ] 6.3 Implement `init-monitoring --yes` confirmation and exact inert shared dry-run behavior, plus `psql --dry-run` plan parity; verify read-only diagnostics/status have no redundant dry-run option.
- [ ] 6.4 Add PTY-based CLI tests proving interactive `odcli psql` inherits streams/signals/history-capable mode, `-c`/`-f` preserve native stdout/stderr/exit code, and normal `psql` rejects document formatting.

## 7. Documentation and Verification

- [ ] 7.1 Add unit tests for validation bounds, every literal formula/order/null tie-break, exact typed/null JSON decoding, secret redaction, ambient `PGOPTIONS` isolation, closed capability/warning keys, mandatory cumulative warnings, session-local partial-extension error classes/stdout suppression with one final JSON, and absence of persistent helper objects.
- [ ] 7.2 Add PostgreSQL integration tests that create a real blocking pair and representative table/index data, then verify locks, stats, estimated bloat, opt-in bounded exact bloat, idempotent monitoring initialization, server status, and `psql -c 'SELECT current_database();'`.
- [ ] 7.3 Update CLI help and README/SDK examples for context resolution, native psql trust/flag restrictions, cumulative counters, approximate bloat, 64 MiB exact default, extension privileges, partial capability warnings, dry-run, and status fallback.
- [ ] 7.4 Run formatting/lint, strict typing, unit and PostgreSQL integration suites, CLI JSON/TOON parity, startup/import budgets, PTY/native psql checks, and the full project PR gate; record commands and results.
- [ ] 7.5 Run the static direct-process/output/type guards and a final `rg` audit to prove there is no new PostgreSQL-specific executor, serializer, dry-run path, direct process launch, `Any`/`object` escape, or moved neighboring domain; document any explicitly accepted remaining violation.
