## Why

Developers currently have to reconstruct PostgreSQL connection details, locate ad hoc SQL, and correlate unrelated catalog views to diagnose blocking, storage, cache use, and bloat. This change adds one project-aware, bounded diagnostics surface and native `psql` access while reusing the shared execution and output boundaries established by GitHub issues #40 and #45.

## What Changes

- Add bounded `odcli db locks`, `db stats`, and `db bloat` snapshots with typed SDK results, stable ordering, capability flags, warnings, and numeric machine values.
- Add explicit, idempotent `odcli db init-monitoring --yes` for only the extensions used by the diagnostics; diagnostics never install extensions implicitly.
- Add `DatabaseResource.psql[_command]()` and `execute_sql[_command]()` on the existing database resource, plus native `odcli psql` argument passthrough with inherited TTY and protected connection identity.
- Extend `odcli postgres status` and `ClusterSnapshot` with one optional, failure-tolerant PostgreSQL server summary.
- Consolidate the useful signals from the existing PostgreSQL SQL scripts into versioned static SDK queries without creating persistent helper objects in user databases.
- Move only the PostgreSQL implementation into a short thematic internal/command package and route every launch, dry-run, and structured result through the shared process/output architecture.
- Add CLI/SDK documentation and unit/integration verification for native `psql`, timeouts, partial capabilities, diagnostics semantics, and secret redaction.

## Capabilities

### New Capabilities

- `postgres-diagnostics`: Bounded lock, statistics, bloat, and opt-in monitoring-extension operations, including typed results and graceful partial capability behavior.

### Modified Capabilities

- `database-management`: Add native `psql` and single-query execution commands to the existing instance-bound `DatabaseResource` using one bound PostgreSQL transport.
- `postgres-cluster`: Add an optional bounded PostgreSQL server summary to the existing cluster snapshot without changing lifecycle ownership rules.
- `cli-odcli`: Add context-aware diagnostics and native `psql` leaves, shared machine output for bounded commands, confirmation/dry-run behavior, and passthrough TTY semantics.

## Impact

- Affects PostgreSQL transport helpers, `DatabaseResource`, `PostgresCluster`, CLI registration/rendering, typed result models, static SQL assets, tests, and user documentation.
- Depends on MYL-68 being completed and merged first so this work consumes the final shared `Command`/`ProcessStep`, dry-run, TTY, and typed output contracts instead of introducing PostgreSQL-specific alternatives.
- Adds no PostgreSQL runtime dependency, daemon, monitoring hierarchy, persistent diagnostic database objects, connection-selector flag, or bespoke executor/serializer.
