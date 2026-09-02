# postgres-diagnostics Specification

## Purpose
TBD - created by archiving change add-postgres-diagnostics-native-psql. Update Purpose after archive.
## Requirements
### Requirement: Database diagnostics resolve one project-bound database

PostgreSQL diagnostics SHALL target the cluster resolved by the current project/environment context and SHALL NOT require a running Odoo process. In a registered worktree, an omitted database SHALL resolve to the single database in generated environment configuration. In a project root, omission SHALL resolve only an unambiguous configured project default. An explicit database SHALL replace only the database name within that same cluster. Missing, empty, or ambiguous identity, an unavailable cluster, a missing `psql` binary, or insufficient privileges SHALL fail with a sanitized actionable error before partial result output.

For an SDK-owned cluster, the diagnostic command SHALL use the shared planned `ensure_running` lifecycle action. For an external cluster, it SHALL only validate reachability and SHALL NOT invoke Docker or attempt lifecycle mutation.

#### Scenario: Stopped Odoo does not block diagnostics

- **WHEN** a registered worktree binds one database, PostgreSQL is reachable, and Odoo is stopped
- **THEN** `locks`, `stats`, and `bloat` resolve that database and execute without starting Odoo

#### Scenario: Explicit database stays on the bound cluster

- **WHEN** a caller supplies database `analytics` from a context bound to host, port, and user for one project cluster
- **THEN** the operation targets `analytics` with the bound host, port, and user and offers no option to replace that connection identity

#### Scenario: Ambiguous project root fails before output

- **WHEN** a project root has no single configured default database and the caller omits the database
- **THEN** the operation spawns no process and returns one actionable ambiguity error without a partial snapshot

### Requirement: Diagnostics use bounded static SQL and typed results

Each diagnostic SHALL execute independently authored, versioned static SQL through the shared PostgreSQL `ProcessStep` executor with `shell=False`, `ON_ERROR_STOP`, server-side statement timeout, subprocess timeout, and a sanitized child environment. Each execution SHALL emit exactly one final JSON value from `psql`; the SDK SHALL decode it into frozen typed result models rather than a generic row mapper.

Machine-size fields SHALL remain numeric bytes, ratios SHALL remain numeric, timestamps SHALL remain typed/ISO-serializable values, and SQL SHALL read the server block size rather than assume 8192 bytes. `top` SHALL default to 20 and accept only integers from 1 through 1000. Result ordering SHALL use deterministic object/PID tie-breakers. Diagnostics SHALL create no persistent schema, view, function, or table and SHALL never install extensions implicitly.

The frozen result schema SHALL use tuples for collections and the following exact fields (Python types also define JSON nullability):

- `DiagnosticWarning(code: Literal["pg_buffercache_not_installed", "pg_buffercache_privilege_denied", "pg_buffercache_query_failed", "pgstattuple_not_installed", "pgstattuple_privilege_denied", "pgstattuple_query_failed", "cumulative_statistics"], message: str)`; messages SHALL be stable, sanitized text selected by `code` and SHALL contain no raw server error.
- `LocksResult(database: str, captured_at: datetime, rows: tuple[LockRow, ...], warnings: tuple[DiagnosticWarning, ...])` and `LockRow(blocked_pid: int, blocking_pids: tuple[int, ...], application_name: str | None, user_name: str | None, client_address: str | None, wait_event_type: str | None, wait_event: str | None, state: str | None, transaction_age_seconds: float | None, query_age_seconds: float | None, query_preview: str)`.
- `StatsCapabilities(pg_buffercache: bool)`; this is the only statistics capability key.
- `StatsSummary(database: str, server_version: str, captured_at: datetime, stats_since: datetime | None, database_bytes: int, block_size_bytes: int)`; `stats_since` is `pg_stat_database.stats_reset` for the selected database and is null when PostgreSQL has not recorded a reset.
- `TableStats(schema: str, table: str, estimated_live_rows: int, heap_bytes: int, toast_bytes: int, index_bytes: int, total_bytes: int, index_count: int, heap_blocks_read: int, heap_blocks_hit: int, index_blocks_read: int, index_blocks_hit: int, shared_buffer_bytes: int | None, shared_buffer_ratio: float | None, hot_page_ratio: float | None)`.
- `IndexStats(schema: str, index: str, table: str, access_method: str, columns: tuple[str, ...], bytes: int, scans: int)`.
- `PostgresStatsResult(summary: StatsSummary, tables: tuple[TableStats, ...], indexes: tuple[IndexStats, ...], capabilities: StatsCapabilities, warnings: tuple[DiagnosticWarning, ...])`.
- `BloatCapabilities(pgstattuple: bool)`; this is the only bloat capability key.
- `TableBloat(schema: str, table: str, total_bytes: int, bloat_bytes: int | None, bloat_ratio: float | None, live_tuples: int | None, dead_tuples: int | None, last_vacuum_at: datetime | None, last_autovacuum_at: datetime | None, last_analyze_at: datetime | None, last_autoanalyze_at: datetime | None, method: Literal["exact", "estimate", "unavailable"])`.
- `IndexBloat(schema: str, index: str, table: str, total_bytes: int, bloat_bytes: int | None, bloat_ratio: float | None, scans: int, unused_candidate: bool, method: Literal["exact", "estimate", "unavailable"])`.
- `PostgresBloatResult(database: str, captured_at: datetime, tables: tuple[TableBloat, ...], indexes: tuple[IndexBloat, ...], capabilities: BloatCapabilities, warnings: tuple[DiagnosticWarning, ...])`.

All byte/count/counter fields SHALL be non-negative. Ratios SHALL be finite and in `[0.0, 1.0]`. A nullable extension-dependent value SHALL be null exactly when it could not be collected; zero remains a measured zero. Missing optional extensions, extension privilege denial, or an optional probe/query failure SHALL be caught inside the static SQL and represented by the corresponding false capability, null dependent fields, and exactly one matching warning code while still emitting one final JSON value. Such degradation SHALL NOT cause a second JSON document, a partially decoded result, or creation of any persistent helper object. Failures of core catalog queries or final decoding SHALL fail the operation instead of returning a partial result.

Each diagnostic script SHALL run in one quiet `psql -X -q -A -t` session and one transaction. It SHALL store intermediate/core rows only in `pg_temp` tables declared `ON COMMIT DROP`. Optional extension relations/functions SHALL be referenced only through dynamic SQL inside server-side `DO` blocks, preventing parse-time failure when absent. Those blocks SHALL catch SQLSTATE `42P01`/`42883` as not installed and `42501` as privilege denied; any other optional invocation error SHALL record the matching `*_query_failed` capability state, while core SQL errors SHALL escape under `ON_ERROR_STOP`. Command tags and notices SHALL be suppressed, and exactly one final JSON-building `SELECT` SHALL write stdout. Commit SHALL drop all session-local state.

#### Scenario: Machine values are not humanized in the SDK result

- **WHEN** `stats` reports a table occupying 1048576 bytes
- **THEN** its typed and JSON/TOON value is the integer `1048576`, while only the Rich projection may display a human-readable size

#### Scenario: Invalid bound does not query PostgreSQL

- **WHEN** `top` is zero, negative, or greater than 1000
- **THEN** validation fails before a PostgreSQL process is spawned

#### Scenario: Diagnostic leaves no database objects

- **WHEN** any read-only diagnostic completes successfully or fails
- **THEN** no persistent diagnostic schema, view, function, or table has been created

### Requirement: Lock diagnostics report active blocking relationships

`DatabaseResource.locks(database, *, top=20, timeout=30.0)` SHALL return a typed bounded snapshot containing only sessions that are currently waiting or blocked. Every row SHALL include blocked PID, blocking PID tuple, available application/user/client identity, wait event, state, transaction age, query age, and a whitespace-normalized query preview capped at 240 characters. Rows SHALL sort by descending wait age, then blocked PID.

The query SHALL derive real blocker relationships from PostgreSQL lock/activity data without installing or depending on a persistent `waitings` view. No extension SHALL be required.

#### Scenario: Blocked session identifies blockers

- **WHEN** one transaction holds a conflicting lock and another session waits for it
- **THEN** `locks` returns the waiting PID with the holder PID in `blocking_pids` plus its wait state and bounded query preview

#### Scenario: No waits returns an empty snapshot

- **WHEN** the database has active sessions but none is waiting or blocked
- **THEN** `locks` succeeds with an empty row tuple rather than returning all `pg_stat_activity` rows

### Requirement: Statistics diagnostics combine size and cumulative activity signals

`DatabaseResource.stats(database, *, top=20, timeout=30.0)` SHALL return `PostgresStatsResult` with the exact schema above. `StatsSummary.stats_since` SHALL carry the selected database's cumulative-counter reset timestamp and SHALL be null only when PostgreSQL reports no reset; capability state SHALL appear only in `capabilities`, not be duplicated in summary.

Each table row SHALL include schema/table, estimated live rows, heap/TOAST/index/total bytes, index count, heap/index read and hit counters, and nullable shared-buffer bytes/ratio and hot-page ratio. Each index row SHALL include schema/index/table, access method, indexed columns, bytes, and scan counter. Read/hit/scan fields SHALL be documented and represented as cumulative PostgreSQL counters, not rates. Tables SHALL be selected and emitted by `total_bytes DESC, schema ASC, table ASC`; indexes independently by `bytes DESC, schema ASC, index ASC`; each list SHALL apply `LIMIT top` after that ordering. The SDK SHALL NOT run exact `count(*)` across Odoo tables.

When `pg_buffercache` is usable, `shared_buffer_bytes` SHALL equal the number of cached main-fork buffers for the table heap relation multiplied by `block_size_bytes`; `shared_buffer_ratio` SHALL equal `min(1.0, shared_buffer_bytes / total_bytes)`, or `0.0` when `total_bytes = 0`; and `hot_page_ratio` SHALL equal cached main-fork buffers with `usagecount >= 3` divided by all cached main-fork buffers for that heap relation, or `0.0` when none are cached. TOAST and index buffers SHALL NOT enter these table ratios.

If `pg_buffercache` is absent or unusable, core size/activity rows SHALL still succeed, cache-dependent fields SHALL be null, `capabilities.pg_buffercache` SHALL be false, and exactly one of `pg_buffercache_not_installed`, `pg_buffercache_privilege_denied`, or `pg_buffercache_query_failed` SHALL explain the classified degradation. When it is usable, the capability SHALL be true, cache fields SHALL contain measured numbers (including zero), and no `pg_buffercache_*` warning SHALL appear.

Every successful `stats` result SHALL contain exactly one `cumulative_statistics` warning because its read/hit/scan fields are cumulative, whether `stats_since` is null or populated.

#### Scenario: Core statistics work without extensions

- **WHEN** `pg_buffercache` is not installed but catalog/statistics views are readable
- **THEN** `stats` returns summary, table, and index core fields with null cache fields, a false capability, and a warning

#### Scenario: Counters retain reset context

- **WHEN** PostgreSQL reports nonzero read/hit/scan counters and a statistics reset timestamp
- **THEN** the result preserves the raw counters and reset context without converting them to rates

### Requirement: Bloat diagnostics distinguish estimates from bounded exact inspection

`DatabaseResource.bloat(database, *, top=20, exact_max_scan_mb=64, timeout=30.0)` SHALL return separate typed `tables` and `indexes`, capabilities, and warnings. Rows SHALL include schema/object/type, total bytes, nullable bloat bytes/ratio, live/dead tuple counters where applicable, vacuum/autovacuum/analyze timestamps, index scans where applicable, and `method` equal to `exact`, `estimate`, or `unavailable`.

The fast table estimate SHALL use `dead_ratio = n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0)`, `bloat_ratio = least(1.0, greatest(0.0, dead_ratio))`, and `bloat_bytes = floor(total_bytes * bloat_ratio)`. The fast index estimate SHALL use the same ratio from its parent table and `floor(index total_bytes * ratio)`. A zero tuple denominator SHALL produce null estimate fields and `method="unavailable"`; otherwise the method SHALL be `estimate`, including measured zero.

Before exact inspection, table candidates SHALL be selected by estimated `bloat_bytes DESC NULLS LAST, total_bytes DESC, schema ASC, table ASC`; indexes independently by estimated `bloat_bytes DESC NULLS LAST, total_bytes DESC, schema ASC, index ASC`; `LIMIT top` SHALL apply to each list. Only those selected rows whose `total_bytes <= exact_max_scan_mb * 1024 * 1024` SHALL be dynamically inspected, in that candidate order. For tables, `pgstattuple(regclass)` SHALL replace the estimate with `dead_tuple_len + free_space` and divide by `table_len` (zero `table_len` yields ratio `0.0`). For B-tree indexes, `pgstatindex(regclass)` SHALL replace it with `floor(total_bytes * leaf_fragmentation / 100)` and ratio `leaf_fragmentation / 100`, clamped to `[0,1]`; other access methods retain their estimate. Final table output SHALL order `bloat_bytes DESC NULLS LAST, total_bytes DESC, schema ASC, table ASC`, and indexes equivalently ending `schema ASC, index ASC`.

The option SHALL accept integers from 0 through 1024; zero SHALL disable exact inspection. `capabilities.pgstattuple` SHALL be true only when both required extension functions can be invoked for eligible supported rows; otherwise it SHALL be false and exactly one of `pgstattuple_not_installed`, `pgstattuple_privilege_denied`, or `pgstattuple_query_failed` SHALL be present. Optional exact failure SHALL retain the corresponding estimate/method for every row rather than fail the result. The dynamic session-local mechanism above SHALL emit only the single final JSON and create no persistent objects.

An index SHALL be marked `unused_candidate=true` only when it is non-primary, non-unique, not replica identity, and has zero recorded scans. The result SHALL warn that cumulative statistics alone do not prove an index is safe to drop.

Every successful `bloat` result SHALL contain exactly one `cumulative_statistics` warning because `scans` and `unused_candidate` depend on cumulative statistics, independently of extension warnings.

#### Scenario: Large relation remains estimated

- **WHEN** a relation is larger than the configured exact scan threshold and `pgstattuple` is available
- **THEN** the relation is not passed to `pgstattuple` and its row is labeled `estimate` or `unavailable`

#### Scenario: Exact capability is optional

- **WHEN** `pgstattuple` is absent or forbidden
- **THEN** `bloat` returns bounded estimated rows with a false capability and warning instead of failing

#### Scenario: Zero threshold disables exact inspection

- **WHEN** `exact_max_scan_mb=0`
- **THEN** no relation is passed to `pgstattuple` and available rows use estimate/unavailable methods

### Requirement: Monitoring extension initialization is explicit, minimal, and idempotent

`DatabaseResource.init_monitoring_command(database, *, timeout=30.0)` SHALL plan an idempotent mutation for SDK-owned clusters only, and `init_monitoring()` SHALL delegate to that command. The frozen `MonitoringInitializationResult` SHALL contain `installed: tuple[Literal["pg_buffercache", "pgstattuple"], ...]`, `already_present` of the same type, and `skipped: tuple[MonitoringExtensionSkip, ...]`, where `MonitoringExtensionSkip(extension: Literal["pg_buffercache", "pgstattuple"], reason: Literal["not_available", "privilege_denied"])`. All three collections SHALL be sorted by extension name and mutually exclusive.

The mutation SHALL use one quiet captured `psql` session, one outer transaction, and one `pg_temp` outcome table. In deterministic extension-name order, a `DO` block SHALL first check `pg_extension`. If the extension is not installed, it SHALL check `pg_available_extensions` before attempting mutation: no matching name SHALL be the only path that records `not_available` and SHALL skip `CREATE EXTENSION`; a matching name SHALL permit identifier-constant dynamic `CREATE EXTENSION` inside a separate PL/pgSQL exception subtransaction. After that positive precheck, only SQLSTATE `42501` SHALL record `privilege_denied`; every other CREATE error, including any `0A000` or post-precheck availability race, SHALL be re-raised under `ON_ERROR_STOP` and SHALL NOT be reported as `not_available`. Thus a caught privilege failure rolls back only that extension attempt and does not undo the other; commit persists successful extensions and drops the temp table. Command tags/notices SHALL be suppressed and one final JSON `SELECT` SHALL be the only stdout.

`installed` means absent before this invocation and successfully created by it; `already_present` means present before this invocation and not recreated; `skipped` means absent and not created solely because the extension is unavailable on the server or creation privilege is denied. Any other SQL/transport/decoding failure SHALL fail the command and SHALL NOT be reported as `skipped`. It SHALL never print credentials.

The operation SHALL NOT install `pageinspect`, `pg_visibility`, or `pgrowlocks` unless a future accepted specification adds a query that requires them. External clusters SHALL be rejected before planning mutation.

#### Scenario: Repeated initialization is idempotent

- **WHEN** monitoring initialization runs twice on the same SDK-owned database with sufficient privileges
- **THEN** the second result lists the already present extensions and creates no duplicate state

#### Scenario: Unavailable extension is not created

- **WHEN** an extension is absent from both `pg_extension` and `pg_available_extensions`
- **THEN** initialization records it as `skipped(reason="not_available")`, does not execute `CREATE EXTENSION` for it, and continues independently with the other extension

#### Scenario: Error after positive availability precheck is not reclassified

- **WHEN** an extension is present in `pg_available_extensions` but its subsequent `CREATE EXTENSION` fails with `0A000` or any error other than `42501`
- **THEN** initialization re-raises the error, rolls back the outer transaction, and does not report `not_available` or a partial-success result

#### Scenario: External cluster is not mutated

- **WHEN** monitoring initialization is requested for an external cluster
- **THEN** the SDK builds no mutating process step and returns an actionable ownership error
