## Context

PostgreSQL behavior is currently split across `resources/postgres.py`, `internal/postgres_transport.py`, `internal/postgres_cli.py`, cluster/compose helpers, and CLI callbacks in both `cli.py` and `commands/db.py`. The existing transport directly calls `subprocess.run`, returns `CompletedProcess | None`, and is already used as an Odoo-unavailable fallback by `DatabaseResource`. `ClusterSnapshot` contains lifecycle, endpoint, container, and resource metrics but no server data.

This change is stage 4 of the architecture roadmap. Implementation starts only after MYL-68 is merged and the feature branch is rebased onto that `main`; the final shared `Command`, `ProcessStep`, process executor, output document, and TTY contracts are prerequisites. GitHub #33 is the functional source of truth, while #40/#45 own the cross-domain execution and output architecture. The AGPL `trobz/odoo-db` project is a behavioral reference only; SQL and Python must be independently authored for this MIT project.

## Goals / Non-Goals

**Goals:**

- Answer blocking, relation/index activity, storage/cache, and bloat questions with bounded, typed, project-aware snapshots.
- Provide native interactive and one-shot `psql` through the existing instance-bound `DatabaseResource` without exposing or accepting alternate connection identity.
- Preserve current PostgreSQL lifecycle status while adding a safe optional server summary.
- Reuse one sanitized PostgreSQL command builder and the shared process/output boundaries for captured, foreground/TTY, and dry-run paths.
- Consolidate only PostgreSQL implementation into `internal/pg/` and `commands/pg.py`, keeping the public resource boundaries intact.

**Non-Goals:**

- A daemon, sampling/history platform, alerting, charts, or cross-project aggregation.
- Automated remediation (`VACUUM FULL`, `REINDEX`, backend cancellation), implicit extension installation, or persistent diagnostic views/functions/tables.
- A PostgreSQL client/service hierarchy, psycopg dependency, query builder, row mapper, custom REPL, or format-specific serializer.
- Moving adjacent domains merely to make the package tree symmetrical.

## Decisions

### 1. Rebase onto the completed shared architecture before implementation

The implementer first verifies MYL-68 is complete, rebases `feat/MYL-70-postgres-diagnostics` onto current `main`, and audits the merged execution/output APIs. All PostgreSQL processes then consume the merged `Command`/`ProcessStep` executor and all bounded results consume the merged typed output document. No compatibility executor, PostgreSQL dry-run adapter, or local serializer is permitted.

Alternative: implement against today's direct `subprocess.run` seams and migrate later. Rejected because it creates exactly the parallel path #40/#45 prohibit and makes preview/TTY parity unverifiable.

### 2. Consolidate transport internals without changing public ownership

Create the short thematic package `odoo_instance_sdk.internal.pg` and move PostgreSQL transport construction, diagnostic orchestration, and versioned SQL assets into it. `commands/pg.py` owns the `postgres` group, the PostgreSQL-related `db` leaves, root `psql`, and their adjacent Rich projections. Existing cluster compose/lifecycle helpers move only when they are PostgreSQL-specific implementation needed by this scope; unrelated command families remain untouched.

`PostgresCluster` remains the sole project-level lifecycle abstraction. `DatabaseResource` remains the instance-bound public database boundary and gains the requested methods. There is no `PostgresClient`, provider interface, factory, or second resource.

Alternative: add a new public diagnostics service. Rejected because it duplicates already-bound database/cluster identity and adds an abstraction with one implementation.

### 3. Use one immutable sanitized psql specification builder

One internal builder accepts the bound host, port, user, database, optional password, native arguments, stdin, timeout, and captured/foreground mode. It resolves `psql`, constructs exact `shell=False` argv/environment/stdin, and returns the private executable specification plus its secret-free `ProcessStep` projection. It strips ambient libpq identity overrides (`PGHOST`, `PGHOSTADDR`, `PGPORT`, `PGUSER`, `PGDATABASE`, service variables, and `PSQLRC`), preserves an explicitly allowed `PGPASSFILE`, and places an explicit password only in the private child environment. It also preserves the current transport boundary by deleting ambient `PGOPTIONS` before setting an SDK-owned server-side statement timeout in the private environment; neither the ambient value nor private timeout value is copied into the public plan.

Connection identity is always appended by the builder. The validator implements the closed grammar in `database-management`: protected identity options and every positional operand are denied; the only one-value options are `-c/-f/-F/-L/-o/-P/-R/-T/-v` and their named long forms; the specified zero-value options are allowed; all other options are rejected. One-value short options accept split or attached values, long options accept split or `=` values, and `--` stops option recognition without permitting a positional identity. This makes `psql` a bounded native-option passthrough, not an open-ended forwarding promise. The displayed plan, fingerprint, repr, exceptions, and output never contain the password. Convenience methods call their `*_command()` sibling and do not rebuild argv.

Alternative: keep separate captured and interactive helpers. Rejected because their environment/identity rules would drift and dry-run could describe a different process from the TTY launch.

### 4. Resolve database and ownership before building a command

CLI context resolution reuses the existing project/cwd rules. In a registered worktree, omitted `DATABASE` selects the one database bound by generated environment config. In a project root, omission is accepted only when `ProjectConfig.default_source_database` identifies one database. An explicit positional database replaces only the database name and cannot replace cluster host/port/user. Missing or ambiguous identity fails before a process is spawned.

Diagnostics never require the Odoo HTTP server. For compose-owned clusters, `psql`, diagnostics, and initialization call the shared planned `ensure_running` action before their query/session. For external clusters, they only perform the established reachability/precondition check and never invoke Docker. `init-monitoring` rejects external clusters before planning mutation.

Alternative: add `--env` or forwarded connection flags. Rejected because both bypass the current context trust boundary and make it easy to target the wrong cluster.

### 5. Execute versioned static SQL and decode one final JSON value

Each bounded diagnostic uses one captured `psql -X -q -A -t` session with `ON_ERROR_STOP`, a server-side `statement_timeout`, and a versioned static SQL script from package resources. The script opens one transaction, creates only `pg_temp` tables with `ON COMMIT DROP`, and runs optional extension references through dynamic SQL inside `DO` blocks so parse-time absence cannot abort the core query. Each optional block catches only SQLSTATE `42P01`/`42883` as not installed and `42501` as privilege denied; any other optional error records the closed `*_query_failed` state, while errors in core SQL escape under `ON_ERROR_STOP`. All command tags/notices are suppressed (`-q`, tuples-only output, warning-level client messages), intermediate statements write only session-local tables, and exactly one final `SELECT json_build_object(...)` writes stdout before commit. No schema-qualified persistent helper is created.

The scripts return integers/floats/timestamps and read `current_setting('block_size')`; they never return `pg_size_pretty()` strings. `top` defaults to 20 and accepts 1–1000. Query previews are capped at 240 characters. `exact_max_scan_mb` defaults to 64, accepts 0–1024, and zero disables exact scans. Result ordering includes deterministic schema/object/PID tie-breakers.

`stats` and `bloat` use the exact formulas, selection, null ordering, and mandatory cumulative warning rules in `postgres-diagnostics`. `pg_buffercache`, `pgstattuple(regclass)`, and `pgstatindex(regclass)` are invoked only by the dynamic optional blocks above. Exact inspection processes only the already selected top rows within the byte threshold in deterministic output order; its values replace estimates. Optional degradation still contributes to the session's single final JSON; core-query or final-decoding failure fails the operation.

Alternative: install persistent views/functions or use psycopg. Rejected because static scripts satisfy the bounded snapshot contract with no database residue or new runtime dependency.

### 6. Keep typed results small and projection-neutral

Add the exact frozen models specified in `postgres-diagnostics`: nullable extension-derived/cache fields are distinct from measured zero; timestamps are typed datetimes; bytes/counts are non-negative integers; ratios are finite `[0, 1]`; collections are tuples. Statistics capabilities contain only `pg_buffercache: bool`; bloat capabilities contain only `pgstattuple: bool`; warnings use the closed stable code vocabulary and sanitized code-selected messages. `StatsSummary.stats_since` is nullable. `PostgresStatsResult` and `PostgresBloatResult` expose the exact named summary/row/capability/warning fields, while `LocksResult`, `MonitoringInitializationResult`, `SqlExecutionResult`, and `PostgresServerInfo` retain their distinct schemas. Rich renders adjacent tables from those same models; JSON and TOON serialize the identical output document.

`execute_sql()` remains deliberately narrow: it returns only return code, stdout, and sanitized stderr for caller-provided SQL and promises no binding or typed rows. Interactive `psql` remains raw passthrough with inherited stdin/stdout/stderr and native exit code, outside document formatting.

Alternative: return dictionaries or introduce a universal SQL result mapper. Rejected because dictionaries weaken the shared typed boundary and a generic mapper creates an application query layer outside scope.

### 7. Add one failure-tolerant server query to cluster status

`postgres status` retains its lifecycle and container/resource collection, then uses one immutable server-summary query template through the same transport and cluster-resolved credentials. Its de-duplicated candidates are the non-empty generated/project-default database, `postgres`, then `template1`; each receives at most one attempt, all attempts share one monotonic total timeout budget, and a candidate receives only the remaining budget. The private child locale is fixed to `C`; server failures use SQLSTATE, and pre-query libpq/executor failures use closed typed categories rather than locale-dependent free text. SQLSTATE `3D000` and candidate query `42501` continue; authentication class `28`, connection class `08`, timeout, missing tool/credentials, and every unclassified query/decoding failure stop. The query uses the exact backend/database formulas in `postgres-cluster` and never exposes the maintenance database.

On failure `server` is null and uses the typed closed reason alias in `postgres-cluster`. Immediate stop reasons win. If every attempted candidate produced only continuable failures, final precedence is `privilege_denied` when any candidate returned `42501`, otherwise `maintenance_database_unavailable` for `3D000`; this is independent of candidate order. Existing cluster fields and exit semantics remain intact. External status performs no Docker inspection.

Alternative: add a separate monitoring hierarchy or fail the whole status command. Rejected because the server data is an optional operational enrichment and lifecycle status remains useful independently.

### 8. Make extension installation explicit and minimal

`init-monitoring` uses one quiet captured `psql` session and one outer transaction with a `pg_temp` outcome table. In deterministic extension-name order, a server-side `DO` block first checks `pg_extension`; when absent there, it checks `pg_available_extensions` before any mutation. Absence from `pg_available_extensions` is the only `not_available` path: it records that outcome and SHALL NOT execute `CREATE EXTENSION`. Only an available, not-yet-installed extension reaches identifier-constant dynamic `CREATE EXTENSION` inside its own PL/pgSQL exception subtransaction. After a positive precheck, only SQLSTATE `42501` is classified as `privilege_denied`; every other CREATE error, including a post-precheck control-file race or any `0A000`, is re-raised under `ON_ERROR_STOP` and is never relabeled `not_available`. A caught privilege failure rolls back only that subtransaction, so the other extension can succeed. Command tags/notices are suppressed and one final JSON `SELECT` is the only stdout. Commit persists successful extensions and drops session-local state. `pageinspect`, `pg_visibility`, and `pgrowlocks` are never attempted.

Alternative: install the historical `init.sql` list or auto-install on reads. Rejected because it expands privileges/state and violates read-only diagnostics.

## Risks / Trade-offs

- [PostgreSQL version/catalog differences break a static query] → Exercise supported PostgreSQL versions in integration tests, keep SQL versioned, and fail with a sanitized actionable error rather than partially decoding output.
- [Exact bloat inspection causes load] → Default to estimates, cap eligible relation size at 64 MiB, cap rows with `top`, apply statement/subprocess timeouts, and allow zero to disable exact scans.
- [Statistics are mistaken for rates or deletion proof] → Encode reset context/capabilities, label counters cumulative in CLI help/docs, use conservative `unused_candidate`, and emit a warning.
- [Optional extension exists but the user lacks access] → Treat availability and usability separately; null only dependent fields and retain core results with a warning.
- [Native options smuggle a second connection identity] → Token-aware validation covers split, attached, and long `=` forms before command construction; tests enumerate rejected aliases.
- [Prerequisite architecture names differ after MYL-68] → Rebase and map this design onto the accepted shared API before editing; if parity cannot be expressed without a PostgreSQL exception, return the contract change to the architecture scope.
- [Package move creates import/startup regression] → Keep CLI registration lazy per MYL-67, move only PostgreSQL files, and run startup/import guards plus static direct-process/output-boundary checks.

## Migration Plan

1. Verify MYL-68 is merged, update local `main`, rebase the feature branch, and record the accepted shared process/output API names.
2. Add typed result models and the immutable PostgreSQL command builder on the shared executor; migrate the existing database-existence probe and cluster server probe first.
3. Add independently authored static SQL assets and typed decoding for locks, stats, bloat, and monitoring initialization.
4. Add `DatabaseResource` command/convenience methods and thin CLI context/rendering callbacks in `commands/pg.py`; then remove superseded direct transport/CLI paths.
5. Run unit, integration, typing, startup, CLI parity, TTY, redaction, and static boundary gates; verify no direct PostgreSQL process/output bypass remains.

Rollback is a normal revert of the feature commit/PR: no diagnostic command creates persistent state except the explicit idempotent extensions. If extension rollback is separately desired, it is an operator decision and this SDK does not drop extensions automatically.

## Open Questions

None. If the merged MYL-68 contract cannot represent inherited TTY and exact sanitized environment/stdin in one immutable plan, implementation stops and the shared architecture issue is amended rather than adding a local exception.
