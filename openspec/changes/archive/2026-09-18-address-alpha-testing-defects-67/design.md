## Context

The current `main` carries thirteen confirmed alpha-testing defects collected in GitHub #67. They touch the CLI output boundary, the monitor inventory, the catalogue migration ledger, restore cluster identity, remote backup ownership, multi-target deletion, Rich time formatting, detached Odoo launch, the VS Code profile, and the SDK-first rule. The repository already has an established CLI output boundary (`commands/`), a canonical `EnvironmentMonitor.snapshot()`, a `PUBLIC_LEAF_CASES` inventory, an immutable command/plan boundary in `internal/proc`, and a frozen `msgspec` model convention. Several of these defects are divergences from those existing contracts rather than greenfield work.

Relevant prior changes: `refactor-cli-output-boundary` established the CLI transport boundary, `OutputMode`, Rich/JSON/TOON, and `env list --watch`; `address-odcli-backlog-53` added project-context `vscode generate`; `centralize-execution-dry-run-typed-output` established `PUBLIC_LEAF_CASES` and the `*_command()` sibling pattern; `add-observability-monitor` established the recursive process tree, Docker metrics, and storage footprint; `2026-09-14-require-core-psutil` made `psutil` a core dependency.

## Goals / Non-Goals

**Goals:**

- Unify the runtime/argv source for `vscode generate` and `run` so `default_run_args` flow into the profile.
- Add `odcli ps` as a typed projection of one monitor snapshot per sample, with a frozen `ProcessInventory` model and a bounded external process-contribution contract.
- Turn `env list` into a `CheckoutInventory` projection that surfaces the main checkout and drops process columns, with a narrow environment-facts entry point.
- Replace the `PRAGMA user_version` migration chain with Alembic + SQLAlchemy Core (no ORM).
- Plan the split of thirteen oversized production files along confirmed responsibility boundaries.
- Make every `PUBLIC_LEAF_CASES` entry carry `sdk_primitive` or a concrete `cli_only_reason`, and add public SDK primitives for the confirmed candidates.
- Fix documentation drift (context-resolution order and removed `--json`), add README badges, and require `pytest.mark.parametrize` for repeated matrices.
- Make `run` resolve worktree paths from `~/.odcli` only.
- Record `project_id` on project-owned remote downloads.
- Add safe variadic multi-target deletion to `backup rm`, `db rm`, and `env rm`.
- Format absolute times in Rich in local timezone.
- Add detached `run -d`.
- Preserve managed PostgreSQL `cluster_id` on COPY restores and report a sanitized primary reason on drop-plan failure.

**Non-Goals:**

- A plugin marketplace, universal lifecycle/provider framework, or dynamic Click command registration.
- Resource budgets, alerts, history, charts, Prometheus, or a sampling daemon.
- Cross-project database deletion, a database UUID, or global name search.
- A daemon manager or second command-construction path for detached run.
- SQLAlchemy ORM or translating repository queries away from `sqlite3`.
- Mass renaming of public API or changing user behavior during the file split.
- FastAPI/dashboard rendering of environment facts (separate task).

## Decisions

### D1: `vscode generate` reuses the resolved runtime/argv source

The VS Code profile generator currently builds its argv independently from `run`. Instead of duplicating the resolution, the generator SHALL call the same shared internal path that produces the resolved runtime/argv for `run` in project context, then project the resulting argv into the launch profile `args`. Disallowed managed-override families are already validated by that path, so the profile inherits the validation. An empty `default_run_args` list adds nothing because the shared source returns no extra arguments.

Alternative considered: copy the `default_run_args` read into the generator. Rejected because it duplicates the source and invites drift, which is exactly the reported defect.

### D2: `odcli ps` projects `ProcessInventory` from one snapshot

`ProcessInventory` is a frozen `msgspec.Struct` built from one `EnvironmentMonitor.snapshot_command()` result per sample. The projection groups snapshot data into three ownership kinds: shared resources (managed PostgreSQL container, shared backend groups, shared external process groups), the main checkout (Odoo process group from `ProjectSummary.runtime` plus checkout storage), and each non-removed environment (Odoo group, attributable backend groups, environment storage, and external process groups). The existing recursive process tree sums CPU/RSS once. The Rich live loop reuses the same `rich.live.Live` foreground pattern as `env list --watch`.

PostgreSQL backend attribution is bounded to `pg_stat_activity` through the existing safe PostgreSQL boundary. Backend PIDs are VM-scoped on macOS Docker Desktop/Colima and host-visible on Linux/external after PID+create-time verification. A backend group attaches to an owner only when database ownership is unambiguous; otherwise it appears once under shared resources with reason `shared_database`.

One bounded frozen process-contribution contract lets `odcli-codex` and Multica add typed process groups. It is not a plugin framework: the contract is a frozen struct with `source`, identity, owner kind/ID, PIDs/scope, CPU/RSS, sample time, and availability. Providers reuse core PID identity/metrics collection and deduplication.

Alternative considered: a new collector and live loop. Rejected because the monitor already owns the canonical snapshot and the live loop pattern exists.

### D3: `env list` becomes `CheckoutInventory`

`CheckoutInventory` is a frozen `msgspec.Struct` built from one `EnvironmentMonitor.snapshot()` per sample plus Git facts of the main checkout. The main checkout is the first row of each project group with `kind = main | environment`. Rich drops `OBSERVED`, `ODOO_PID`, `CPU`, `RAM`, `SIZE`, and detailed process/artifact columns; those live in `odcli ps`. Rich remains a readable `Table` on normal and compact widths; it does not collapse to `branch=... state=...` blocks.

One narrow environment-facts entry point is a Python entry point returning a typed callable/protocol. A provider receives immutable core checkout rows and returns frozen summaries. Collection has deterministic order, a short bounded timeout, and isolated per-provider/per-row errors. Each provider adds at most one Rich column. JSON/TOON nest summaries under a stable provider ID.

Alternative considered: keep `env list` as the process+checkout table and add a separate `checkout ls`. Rejected because #67 explicitly asks for two different commands with different answers.

### D4: Alembic + SQLAlchemy Core replace the PRAGMA chain

One first Alembic revision creates the full current schema in a single step. Clean installs apply only that revision. Known alpha catalogues are backed up via SQLite `.backup`, verified, and stamped. After the transition, the old `PRAGMA user_version` ledger, `_run_migrations()`, `_migrate_v*`, and stale compatibility branches are removed. SQLAlchemy Core is used for schema metadata and Alembic integration only; ORM is not added and `sqlite3` queries remain.

The first revision materializes the current schema, including restore/event tables, `environment_runtime`, `source_git_branch`, repaired environment child foreign keys, and unified-root path columns. Historical sequential PRAGMA requirements in other capabilities are not production steps after this change; they describe schema that the first revision already contains.

Alternative considered: keep the PRAGMA chain and add Alembic alongside. Rejected because it keeps the historical chain in production code and the issue explicitly asks to remove it.

### D5: File split along confirmed responsibility boundaries

The thirteen oversized files are split behaviour-preservingly along the boundaries listed in #67. A Python package is created only when confirmed multiple responsibilities exist. The split is ordered by dependency safety: `models.py` first (domain split with public re-exports), then leaf modules, then `cli.py`/`commands/env.py` (thin registration). A simple CI check rejects manually maintained production Python files over 1000 physical lines with generated/vendor exclusion and no existing-file allowlist.

Target module tree (preliminary, to be confirmed by call-graph analysis during implementation):

| Source file | Confirmed boundaries | Target modules |
|---|---|---|
| `resources/environment.py` (3838) | checkout planning/execution, applied settings, cleanup/recovery, pgAdmin integration | `resources/environment/checkout.py`, `resources/environment/settings.py`, `resources/environment/cleanup.py`, `resources/environment/pgadmin.py` |
| `storage/backup_catalog.py` (2307) | backup operations, environment ops, runtime ops, cluster ops (after Alembic) | `storage/catalog/backup.py`, `storage/catalog/environment.py`, `storage/catalog/runtime.py`, `storage/catalog/cluster.py` |
| `resources/instance.py` (2103) | runtime identity, command planning, foreground lifecycle, logs, auxiliary restore session | `resources/instance/identity.py`, `resources/instance/planning.py`, `resources/instance/foreground.py`, `resources/instance/logs.py`, `resources/instance/restore_session.py` |
| `internal/database_preparation.py` (1923) | source resolution, verified backup materialization, preflight, orchestration | `internal/dbprep/source.py`, `internal/dbprep/materialize.py`, `internal/dbprep/preflight.py`, `internal/dbprep/orchestrate.py` |
| `cli.py` (1691) | thin registration only; move callbacks to existing command modules | `cli.py` (registration only), `commands/` modules absorb callbacks |
| `resources/monitor.py` (1667) | snapshot planning, collection, typed projection | `resources/monitor/planning.py`, `resources/monitor/collection.py`, `resources/monitor/projection.py` |
| `resources/database.py` (1521) | lifecycle, backup/restore, diagnostics, monitoring SQL | `resources/database/lifecycle.py`, `resources/database/backup_restore.py`, `resources/database/diagnostics.py` |
| `resources/postgres.py` (1500) | lifecycle, backup/restore, diagnostics, monitoring | `resources/postgres/lifecycle.py`, `resources/postgres/backup_restore.py`, `resources/postgres/diagnostics.py` |
| `commands/env.py` (1393) | checkout/list/show/path/remove/sync and Rich projections | `commands/env/checkout.py`, `commands/env/list.py`, `commands/env/show.py`, `commands/env/remove.py`, `commands/env/sync.py` |
| `internal/doctor.py` (1386) | manifest/runtime/catalog/PostgreSQL/environment checks | `internal/doctor/manifest.py`, `internal/doctor/runtime.py`, `internal/doctor/catalog.py`, `internal/doctor/postgres.py`, `internal/doctor/environment.py` |
| `models.py` (1215) | models by existing domains with public re-exports | `models/` package with domain submodules, `models.py` re-exports public names |
| `internal/database_replacement.py` (1189) | planning/validation/execution (if call graph confirms) | `internal/dbreplace/planning.py`, `internal/dbreplace/validation.py`, `internal/dbreplace/execution.py` |
| `internal/proc/executor.py` (1153) | run/spawn/termination (if call graph confirms) | `internal/proc/run.py`, `internal/proc/spawn.py`, `internal/proc/terminate.py` |

Dependency direction: `commands/` → `resources/` → `internal/` → `internal/proc/`. `models.py` (or `models/` package) is imported by all layers but imports none. No circular dependencies. Public SDK imports are preserved via re-export shims where needed.

Alternative considered: mechanical line-count split. Rejected because it creates artificial modules and breaks cohesion.

### D6: SDK-first via `PUBLIC_LEAF_CASES` extension

Each current `PUBLIC_LEAF_CASES` entry, including the new `ps` leaf, gets either `sdk_primitive` or `cli_only_reason`. New public SDK primitives are added for `backup inspect`, `db ls`, the shared test runner, `deps verify`, persisted `stop`, and COPY database replacement, each with frozen input/result types and `*_command()` siblings. The contract test rejects a leaf without one of the two values. An architecture gate rejects a Click callback that builds a self-contained domain operation through `internal.*` where a public SDK primitive applies.

Alternative considered: a second registry. Rejected because `PUBLIC_LEAF_CASES` is already the single inventory and #67 explicitly forbids a second one.

### D7: Canonical worktree path only

`run` and all execution/ownership paths resolve worktree paths from `~/.odcli`. The legacy `~/Library/Application Support/odoo-instance-sdk` path remains only in the one-time migration/compatibility code. A regression test verifies `run --dry-run` builds plans against the canonical path with no legacy symlink. The repository is searched to confirm the legacy path is not used outside migration/compatibility modules.

Alternative considered: keep the compatibility symlink indefinitely. Rejected because it hides the dependency and causes the reported runtime/session divergence.

### D8: Project-owned remote download records `project_id`

All project-owned download flows pass the canonical `project_id` into `start_download()` before HTTP transfer. The preparation flow already computes `project_id` for lock/catalog context; it now passes it to the created backup row. Generic SDK backup without project context stays unowned. An explicit safe relink/repair path exists for already-unowned rows. The existing test that manually injects `_RuntimeBinding` is replaced by a test that exercises the real preparation call chain.

Alternative considered: infer ownership from environment/restore joins. Rejected because #67 explicitly forbids indirect ownership inference.

### D9: Safe variadic multi-target deletion

`backup rm`, `db rm`, and `env rm` accept variadic positional arguments and reuse the existing single-target resolvers/validators/builders. One planning preflight resolves all targets; one confirmation lists all sanitized targets; sequential execution with per-target revalidation; per-target results in one document; non-zero exit on partial failure. `--dry-run` returns one ordered aggregate plan. Single-target calls stay backward-compatible. No generic bulk SDK, parallel deletion, or new orchestration hierarchy is added.

Alternative considered: a generic bulk operation. Rejected because #67 explicitly forbids it and the three resource types have different ownership/cleanup contracts.

### D10: Rich local-time formatting

One small helper in the existing internal formatting module converts aware UTC or naive SQLite UTC timestamps to local timezone via `datetime.astimezone()` and formats as `YYYY-MM-DD HH:MM`. Durations are untouched. JSON/TOON keep full ISO precision. No timezone dependency, locale/config option, or separate formatting layer is added.

Alternative considered: a formatting layer with locale support. Rejected as over-engineering for the reported need.

### D11: Detached `run -d`

`-d, --detach` is a separate public SDK launch command with a `*_command()` sibling. It spawns Odoo through the existing `internal/proc` executor, confirms the process is alive, persists runtime identity, and returns PID/identity/endpoint/log path without waiting. The convenience method delegates to that captured command and does not rebuild argv, cwd, environment, or actions. Detached launch SHALL NOT overload `run_foreground()`, which continues to inherit stdio and block until Odoo exits. No daemon manager or second command-construction path. Logs go to the bound `odoo.conf` logfile; no logfile means fail-fast before spawn. `odcli stop` stops the persisted runtime. Foreground behavior is unchanged. `-d` after the literal `--` is a native Odoo argument.

Alternative considered: a new background manager. Rejected because the existing runtime identity and stop mechanism already support persisted runtimes.

Alternative considered: add a `detach=` flag to `run_foreground()`. Rejected because that method's contract is blocking inherited stdio, and mixing it with immediate return would hide two process lifetimes behind one name.

### D12: COPY-restore cluster identity and primary reason

`EnvironmentManager._do_copy_restore()` binds the active managed cluster identity so `record_restore()` stores `cluster_id`. The failure path that currently swallows the drop-plan error as `None` is replaced with a sanitized primary reason while retaining fail-closed behavior.

Alternative considered: relax the guarded drop check. Rejected because it weakens safety.

### D13: Context resolution and JSON selector match the shipped CLI

Issue #67 item 7 records two documentation/spec drifts, not new product behavior. `Project resolution order` on main lists nearest manifest before exact registered worktree, contradicting `Context-aware command resolution` and the code. This change rewrites that requirement to: explicit `--project` → exact registered worktree → nearest manifest → error, which is the project-identity projection of `--env` → exact worktree → `--project`/nearest manifest.

On main, `Stable machine output` still requires a `--json` alias, which contradicts both `Single format selector` and the issue. This change removes that alias from the machine-output contract so archive does not restore it. `--format json` is the only JSON selector; removed `--json` is a Click usage error.

## Risks / Trade-offs

- **`env list` column reduction may surprise users** → The removed columns are available in `odcli ps`; document the split in README and help.
- **Alembic migration of known catalogues may fail on edge schemas** → SQLite `.backup` first; stamp only after schema-equivalence verification; explicit data migrations remain manual.
- **File split may break public imports** → Preserve public re-exports; run focused tests, architecture contract tests, Ruff, and mypy after each slice.
- **`ProcessInventory` and `CheckoutInventory` add public types** → They are additive frozen models; existing snapshot consumers are unchanged.
- **Detached run may leave stale runtime records** → Immediate-exit detection and the existing stale-runtime recognition handle this; `odcli stop` works on persisted runtimes.
- **Multi-target deletion partial failure** → Per-target results and non-zero exit make partial failure explicit; no atomicity promise across files/PostgreSQL/Git.
- **Environment-facts provider timeout** → Bounded timeout and isolated per-provider errors; missing provider is not an error.
- **Legacy path removal may break users with old symlinks** → The one-time migration code remains; only non-migration usage is removed.

## Migration Plan

1. Work on `feat/issue-67-alpha-testing-defects`.
2. Characterize current behavior with tests where coverage is missing.
3. Apply functional slices in dependency-safe order: VS Code `default_run_args`; `run` canonical path; remote backup `project_id`; COPY-restore `cluster_id`; Rich time formatting; `odcli ps`; `env list` `CheckoutInventory`; multi-target deletion; detached `run -d`; SDK-first boundary; Alembic migration; file split; documentation/badges.
4. Run focused tests after each slice and full `make pr` at handoff.
5. For Alembic: back up known alpha catalogues, verify, stamp, then remove the old migrator.

Rollback is commit-wise. The Alembic migration is reversible by reverting the stamping commit before the old migrator is removed.

## Open Questions

None. The capability boundaries, inventory contracts, migration strategy, and deletion semantics are fixed above.