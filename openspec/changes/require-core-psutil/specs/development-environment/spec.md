## MODIFIED Requirements

### Requirement: Catalog current-runtime record (schema v8 → v9)

Catalog MUST хранить одну current runtime-запись на environment в таблице
`environment_runtime` (schema migration v8 → v9, `CURRENT_SCHEMA_VERSION = 9`).

`BackupCatalog` MUST предоставлять read-only
`list_environments_with_runtimes()` returning each environment and its current
runtime from one SQLite read snapshot using two SELECTs in that transaction, plus `get_environment_runtime()` and
`list_environment_runtimes()` for their explicit read-only callers, and write
`upsert_environment_runtime(...)` / `clear_environment_runtime(environment_id)`
(только из `run_foreground`).

Collector (`EnvironmentMonitor`) reads the aggregate rows read-only. PID safety:
collector считает process живым только при
`psutil.Process(pid).create_time() == recorded_create_time` и
`psutil.pid_exists(pid)`; mismatch → `runtime.state="stopped"`.

#### Scenario: Migration adds runtime table

- **WHEN** catalog at schema v8 is opened
- **THEN** `environment_runtime` table is created, `PRAGMA user_version = 9`, existing environments have no runtime row

#### Scenario: Upsert is one-row-per-environment

- **WHEN** `upsert_environment_runtime(env_id, ...)` is called twice for the same environment
- **THEN** one row exists with the latest values (no duplicates)

#### Scenario: Collector reads one aggregate snapshot

- **WHEN** `EnvironmentMonitor.snapshot()` runs
- **THEN** it calls `list_environments_with_runtimes()` once and never calls
  `upsert`/`clear`

#### Scenario: Collector reads runtime read-only

- **WHEN** `EnvironmentMonitor.snapshot()` runs
- **THEN** its aggregate catalog read is read-only; collector never calls
  `upsert`/`clear`
