## 1. VS Code `default_run_args` propagation (item 1)

- [x] 1.1 Add a regression test that generates a profile from a project with `default_run_args = ["--dev=qweb,xml"]` and asserts `--dev=qweb,xml` appears in `args` exactly once.
- [x] 1.2 Add a regression test for an empty `default_run_args` list asserting no extra arguments.
- [x] 1.3 Refactor `vscode generate` to reuse the same resolved runtime/argv source as `run` in project context; preserve disallowed-override validation.
- [x] 1.4 Verify managed-environment context behavior is unchanged; run focused tests, Ruff, and mypy.

## 2. `odcli ps` process/resource inventory (item 2)

- [x] 2.1 Add the frozen `ProcessInventory` typed model and its subtypes (process groups, backend groups, external contributions) in `models.py`.
- [x] 2.2 Implement `EnvironmentMonitor.processes_command()` and `processes()` projecting one canonical snapshot per sample.
- [x] 2.3 Implement PostgreSQL backend attribution bounded to `pg_stat_activity` with Linux/macOS PID-scope modelling and `shared_database` handling.
- [x] 2.4 Implement the bounded process-contribution contract for external sources without a plugin framework.
- [x] 2.5 Register `odcli ps` as a bounded structured leaf with `--all-projects`, `--watch`, `--interval`, `--format`, and `--fields`; delegate to the SDK primitive.
- [x] 2.6 Implement Rich rendering with shared resources, main checkout, and environment blocks; reuse the live loop pattern.
- [x] 2.7 Add tests covering stopped/running main checkout, multiple Odoo workers, unique/shared database, multiple PostgreSQL connections, Linux/macOS PID scope, privilege failure, stale PID, shared external process, and no double counting.

## 3. `env list` checkout inventory (item 3)

- [x] 3.1 Add the frozen `CheckoutInventory` typed model with `kind = main | environment` rows.
- [x] 3.2 Implement `CheckoutInventory` projection from one snapshot per sample plus Git facts of the main checkout.
- [x] 3.3 Refactor `odcli env list` Rich to drop `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, `SIZE`, and detailed process/artifact columns; keep a readable `Table` on normal and compact widths.
- [x] 3.4 Add the main checkout as the first typed row of each project group without creating a synthetic environment.
- [x] 3.5 Implement the narrow environment-facts entry point (typed callable/protocol, Python entry points, deterministic order, bounded timeout, isolated errors, at most one Rich column per provider).
- [x] 3.6 Update `env list --watch` to use one snapshot per sample and the same table contract.
- [x] 3.7 Add tests covering main + environments, all-projects, stopped runtime, Git ahead/diff, no extensions, three installed providers, one failed/slow provider, stable order, and watch interruption.

## 4. Alembic + SQLAlchemy Core catalogue migration (item 4)

- [x] 4.1 Add `alembic` and `sqlalchemy` (Core, no ORM) to runtime dependencies; refresh the uv lock.
- [x] 4.2 Create the first Alembic revision that creates the full current schema, constraints, and indexes in one step.
- [x] 4.3 Implement clean-install application of only the first revision without running v2–v16.
- [x] 4.4 Back up known alpha catalogues, verify schema equivalence, and stamp the first revision.
- [x] 4.5 Remove the old `PRAGMA user_version` ledger, `_run_migrations()`, `_migrate_v*`, intermediate schema fixtures, and stale compatibility branches.
- [x] 4.6 Add a CI gate rejecting multiple Alembic heads and unverified schema-metadata divergence.
- [x] 4.7 Add tests covering fresh install, supported alpha-catalogue transition, repeated run, migration failure, foreign keys/indexes, and data writes after upgrade.

## 5. Split oversized production Python files (item 5)

- [x] 5.1 Confirm responsibilities, callers, and public imports for each of the thirteen listed files.
- [x] 5.2 Prepare the target module tree in this `design.md` (already done) and proceed with behaviour-preserving slices.
- [x] 5.3 Split `models.py` by domain with public re-exports preserved.
- [x] 5.4 Split `resources/environment.py`, `storage/backup_catalog.py` (after Alembic), `resources/instance.py`, `internal/database_preparation.py`, `cli.py`, `commands/env.py`, `resources/monitor.py`, `resources/database.py`, `resources/postgres.py`, `internal/doctor.py`, `internal/database_replacement.py`, and `internal/proc/executor.py` along confirmed boundaries.
- [x] 5.5 Add a CI line-limit check rejecting manually maintained production Python files over 1000 physical lines with generated/vendor exclusion and no existing-file allowlist.
- [x] 5.6 Run focused tests, architecture contract tests, Ruff, and mypy after each slice.

## 6. CLI through public typed SDK primitives (item 6)

- [x] 6.1 Extend `PublicLeafCase` with `sdk_primitive` and `cli_only_reason` fields; update every existing entry and add `ps` so the inventory stays the single source of truth.
- [x] 6.2 Add public frozen input/result types and `*_command()` siblings for `backup inspect`, `db ls`, the shared test runner, `deps verify`, persisted `stop`, and COPY database replacement.
- [x] 6.3 Align `test` and `module test` on one SDK test primitive.
- [x] 6.4 Add a contract test rejecting a leaf without `sdk_primitive` or `cli_only_reason` and an architecture gate rejecting parallel domain execution in Click callbacks.
- [x] 6.5 Update `docs/python-sdk.md`, README, and execution-boundary from the actual public API.

## 7. SDK-first rule and documentation drift (item 7)

- [x] 7.1 Add the SDK-first rule to `AGENTS.md` and the OpenSpec contract.
- [x] 7.2 Unify context-resolution order across README, docs, and code: explicit `--env` → exact registered worktree → explicit `--project`/nearest manifest → error.
- [x] 7.3 Remove the removed `--json` alias wording from `docs/execution-boundary.md`, README, and characterization contracts; `--format json` is the only JSON selector and `--json` is a Click usage error.
- [x] 7.4 Add CI, MIT, and Python 3.12+ badges to README.
- [x] 7.5 Add the `pytest.mark.parametrize` rule for repeated input/output/error matrices to `AGENTS.md`/`CONTRIBUTING.md`.
- [x] 7.6 Sync delta specs and archive completed OpenSpec changes of this slice; leave unrelated/incomplete changes intact.

## 8. `run` uses canonical `~/.odcli` worktree path (item 8)

- [x] 8.1 Add a regression test that builds `run --dry-run` against a catalogue existing only under `~/.odcli` with no legacy path or symlink.
- [x] 8.2 Fix `run` to resolve worktree paths, `cwd`, `--addons-path`, and Git provenance from the canonical root.
- [x] 8.3 Verify runtime/session ownership, port preflight, and active-session lookup agree across `env list` and `run`.
- [x] 8.4 Search the repository for legacy path usage outside migration/compatibility code and remove it.

## 9. Project remote-backup ownership (item 9)

- [x] 9.1 Add a CLI/workflow-level regression test that runs a project download through the same path as `odcli db refresh` and asserts a non-null canonical `backups.project_id`.
- [x] 9.2 Pass the canonical `project_id` into `start_download()` before HTTP transfer in all project-owned download flows.
- [x] 9.3 Verify the new backup is visible in `odcli backup ls` of the current project and absent from another project's list; present with `--all-projects`.
- [x] 9.4 Preserve generic unowned SDK backup; add an explicit safe relink/repair path for already-unowned rows.
- [x] 9.5 Remove the test dependency on manual `_RuntimeBinding` injection; exercise the real preparation call chain.

## 10. Safe multi-target deletion (item 10)

- [x] 10.1 Make `backup rm` accept variadic UUIDs, reusing single-target resolvers and binding checks.
- [x] 10.2 Make `db rm` accept variadic database names within one resolved project cluster; preserve `--force-default` and `--force-connections` per-database checks.
- [x] 10.3 Make `env rm` accept variadic UUIDs/selectors with per-target repository/cluster resolution; preserve no-argument cwd semantics.
- [x] 10.4 Implement one planning preflight, one confirmation, sequential execution with per-target revalidation, per-target results, and non-zero exit on partial failure.
- [x] 10.5 Implement `--dry-run` returning one ordered aggregate plan.
- [x] 10.6 Add tests covering multi-target success, unknown/duplicate target abort, partial failure, single-target backward compatibility, cross-project UUIDs, cluster-scoped db names, shared vs copy environment cleanup, and machine-mode `--yes` behavior.

## 11. Rich absolute-time formatting (item 11)

- [x] 11.1 Add one small helper in the existing internal formatting module converting aware UTC and naive SQLite UTC timestamps to local timezone `YYYY-MM-DD HH:MM`.
- [x] 11.2 Apply the helper to `backup ls` and `backup inspect` time fields; audit other Rich renderers for absolute timestamps.
- [x] 11.3 Add a parametrized test covering aware UTC, SQLite naive UTC, and local timezone with non-zero offset including day-boundary crossing; verify JSON/TOON keep ISO precision and durations are untouched.

## 12. Detached Odoo launch (item 12)

- [x] 12.1 Add `-d, --detach` to `odcli run` and a public SDK detached launch `*_command()` sibling that does not overload `run_foreground()`; preserve foreground behavior.
- [x] 12.2 Implement spawn, alive confirmation, runtime identity persistence, and return of PID/identity/endpoint/log path without waiting.
- [x] 12.3 Ensure no logfile fails fast before spawn; `iter_logs` works after detached launch.
- [x] 12.4 Implement `--dry-run -d` showing the sanitized process command and detached lifecycle plan without spawning.
- [x] 12.5 Reject incompatible combinations with native Odoo args and output formats through Click validation; treat `-d` after `--` as native.
- [x] 12.6 Add lifecycle/contract tests covering detached launch, immediate exit, stop, logs, dry-run, and foreground unchanged.

## 13. COPY-restore cluster identity (item 13)

- [x] 13.1 Bind the active managed cluster identity in `EnvironmentManager._do_copy_restore()` so `record_restore()` stores `cluster_id`.
- [x] 13.2 Replace the `None`-swallowing failure in `_remove_copy_database_command()` with a sanitized primary reason; retain fail-closed behavior.
- [x] 13.3 Add a test covering fresh backup → restore → COPY checkout → stop → `env rm --dry-run` showing guarded drop step → `env rm --yes` confirming COPY-DB and owned-artifact cleanup.
- [x] 13.4 Verify dirty worktree, active runtime, cluster/volume identity, restore binding, and related-resource checks are not weakened.

## 14. Compatibility, documentation, and delivery gates

- [ ] 14.1 Update README/CLI documentation for `odcli ps`, `CheckoutInventory`, `run -d`, multi-target deletion, local-time formatting, Alembic, badges, and the SDK-first rule.
- [ ] 14.2 Run focused characterization, monitor, output-parity, Live, security/redaction, FastAPI, frontend-build, and packaging tests; record skipped external prerequisites separately from regressions.
- [ ] 14.3 Run the full `make pr` gate and `uv build`; inspect wheel/sdist contents and metadata.
- [ ] 14.4 Review `git diff` for scoped changes, verify Conventional Commit messages, and ensure no unrelated feature work is included.
- [ ] 14.5 Open the implementation PR from `feat/issue-67-alpha-testing-defects` with `#67` in the title/body, link GitHub issue #67, and report local `make pr`/build results.