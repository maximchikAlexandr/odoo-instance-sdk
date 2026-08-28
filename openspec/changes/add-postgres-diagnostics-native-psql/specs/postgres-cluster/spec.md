## ADDED Requirements

### Requirement: Cluster snapshot includes an optional PostgreSQL server summary

The typed `ClusterSnapshot` used by `odcli postgres status` SHALL preserve its existing mode, ownership, lifecycle state, endpoint, container, CPU/memory/volume, unavailability, and sampling fields and SHALL add `server: PostgresServerInfo | None` plus `server_unavailability_reason: ServerUnavailabilityReason | None`, where `ServerUnavailabilityReason = Literal["psql_missing", "credentials_missing", "server_unreachable", "maintenance_database_unavailable", "authentication_failed", "privilege_denied", "timeout", "query_failed", "invalid_response"]`.

`PostgresServerInfo` SHALL be one frozen typed model with `version: str`, `postmaster_started_at: datetime`, `uptime_seconds: int`, `connections_total: int`, `connections_active: int`, `connections_idle: int`, `max_connections: int`, and `connectable_databases: int`; none of these fields is nullable after successful decoding. Every candidate attempt SHALL execute the same single-statement read-only query template. All attempts SHALL share one positive finite monotonic total timeout budget supplied to status collection: before each attempt the transport SHALL compute remaining time, use no more than it for both server statement and subprocess timeout, and return `timeout` without another attempt once exhausted. Thus collection performs at most one query per candidate and at most three queries total, never three full independent timeout windows. It SHALL NOT duplicate detailed lock/statistics/bloat results.

The query SHALL compute fields exactly as follows, excluding its own `pg_backend_pid()` row: `connections_total` is `count(*)` from `pg_stat_activity` where `backend_type = 'client backend'`; `connections_active` is the subset with `state = 'active'`; `connections_idle` is the subset with `state = 'idle'` (idle-in-transaction and disabled states remain in total but neither active nor idle). `connectable_databases` is `count(*)` from `pg_database` where `datallowconn`, `NOT datistemplate`, and `has_database_privilege(current_user, datname, 'CONNECT')` are all true. `uptime_seconds` is the non-negative integer floor of `extract(epoch FROM clock_timestamp() - pg_postmaster_start_time())`.

Status SHALL use the cluster's already resolved host, port, user, password/allowed passfile, and TLS/service-independent environment; it SHALL NOT borrow credentials from an arbitrary Odoo database. The maintenance database candidate list SHALL be deterministic and de-duplicated: the non-empty database already bound by generated environment/project default configuration first, then `postgres`, then `template1`. Absence of a project default therefore starts with `postgres`. Each candidate SHALL be attempted at most once.

Classification SHALL be locale-independent: the private client locale SHALL be `C`; server errors SHALL use SQLSTATE; pre-query failures SHALL be mapped by typed missing-tool/credential/timeout/executor connection categories, never by copying or exposing free-form stderr. SQLSTATE `3D000` (invalid catalog name) and query SQLSTATE `42501` (insufficient privilege on that candidate) SHALL record a candidate failure and continue if budget/candidates remain. Authentication SQLSTATE class `28` SHALL stop as `authentication_failed`; connection exception class `08` SHALL stop as `server_unreachable`; timeout SHALL stop as `timeout`; any other query error SHALL stop as `query_failed`; successful process output that cannot decode SHALL stop as `invalid_response`. Missing binary/credentials SHALL stop before attempts as `psql_missing`/`credentials_missing`. The selected maintenance database and all raw diagnostics SHALL remain private.

If the server query succeeds, `server` SHALL be populated and the reason SHALL be null. An immediate stop reason above SHALL be final. If all attempted candidates fail only with continuable SQLSTATEs, the final reason SHALL be `privilege_denied` when any attempt returned `42501`; otherwise it SHALL be `maintenance_database_unavailable` (all returned `3D000`). This precedence SHALL apply regardless of candidate order. The typed reason SHALL contain no exception text, endpoint, database, user, SQL, or credential; detailed sanitized diagnostics MAY be emitted separately. All existing cluster fields SHALL remain available and existing status exit semantics SHALL remain unchanged. External cluster collection SHALL never perform Docker inspection.

#### Scenario: Healthy server summary is populated

- **WHEN** cluster lifecycle status and the bounded server query succeed
- **THEN** one snapshot contains all existing cluster fields plus typed version, uptime, connection, limit, and database-count values

#### Scenario: Missing psql degrades only server information

- **WHEN** lifecycle/endpoint status is available but `psql` is not installed
- **THEN** the snapshot retains existing cluster data, sets `server` to null, and reports a sanitized missing-tool reason

#### Scenario: External cluster avoids Docker

- **WHEN** status is collected for an external reachable PostgreSQL cluster
- **THEN** the optional server query may run through the bound transport but no Docker inspect, stats, start, or stop operation occurs

#### Scenario: Missing project database uses maintenance fallback

- **WHEN** no generated/default project database is bound and `postgres` is unavailable to the bound cluster credentials but `template1` is connectable
- **THEN** status tries `postgres` then `template1`, returns the server summary from `template1`, and does not expose the maintenance database

#### Scenario: Every maintenance database is unavailable

- **WHEN** each de-duplicated maintenance candidate is absent or forbidden while the endpoint remains reachable
- **THEN** existing status data and exit semantics are preserved, `server` is null, and the reason is `privilege_denied` if any candidate returned `42501`, otherwise `maintenance_database_unavailable`

#### Scenario: Candidate attempts share one deadline

- **WHEN** the first candidate consumes most of the total status-query timeout and returns continuable SQLSTATE `3D000`
- **THEN** the next candidate receives only the remaining budget and no attempt starts after that budget reaches zero
