## MODIFIED Requirements

### Requirement: `OdooClient.environments` facade

`OdooClient` MUST expose `environments: EnvironmentResource` наравне с `instance` и `backups`. Catalog открывается internally и не экспортируется как `client.catalog`.

```text
OdooClient
├── instance          # InstanceFactory
├── backups           # BackupResource
└── environments      # EnvironmentResource
```

`EnvironmentResource.list()` остаётся источником environment rows для SDK callers. `EnvironmentMonitor` reads `BackupCatalog.list_environments` / `list_environment_runtimes` directly (via `get_catalog_path()` or injected `catalog_path`) and MUST NOT reimplement catalog schema or scan the filesystem. `odcli env list` / `odcli monitor` consume `EnvironmentMonitor.snapshot()`. `EnvironmentResource` does not grow runtime methods; `environment_runtime` is catalog-internal.

#### Scenario: Three facades

- **WHEN** `OdooClient` constructed
- **THEN** `client.instance`, `client.backups`, `client.environments` доступны; `client.catalog` отсутствует

#### Scenario: Environment not found

- **WHEN** `client.environments.get(uuid)` для несуществующего ID
- **THEN** `EnvironmentNotFoundError`

#### Scenario: Environment conflict

- **WHEN** checkout для repo+branch с уже active environment
- **THEN** `EnvironmentConflictError` с code и details

### Requirement: Catalog current-runtime record (schema v8 → v9)

Catalog MUST хранить одну current runtime-запись на environment (одна строка на environment_id, upsert) в новой таблице `environment_runtime`:

```sql
CREATE TABLE IF NOT EXISTS environment_runtime (
    environment_id TEXT PRIMARY KEY REFERENCES environments(id),
    root_pid INTEGER NOT NULL,
    create_time REAL NOT NULL,
    started_at TEXT NOT NULL,
    checkout_branch TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    http_url TEXT NOT NULL,
    http_port INTEGER NOT NULL,
    database_name TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Schema migration v8 → v9 (additive: новая таблица, существующие columns не меняются). `CURRENT_SCHEMA_VERSION` становится `9`. Migration idempotent (`CREATE TABLE IF NOT EXISTS`).

`BackupCatalog` MUST предоставлять read-only:

- `get_environment_runtime(environment_id) -> Row | None`;
- `list_environment_runtimes() -> list[Row]` (для collector; одна транзакция с `list_environments`).

И write (только из `run_foreground`, не из CLI command body и не из collector):

- `upsert_environment_runtime(environment_id, *, root_pid, create_time, started_at, checkout_branch, commit_sha, http_url, http_port, database_name) -> None`;
- `clear_environment_runtime(environment_id) -> None`.

Collector (`EnvironmentMonitor`) and `odcli env list` (via snapshot) read `get_environment_runtime`/`list_environment_runtimes` read-only. Collector MUST NOT write `environment_runtime`. Stale-row cleanup is `run_foreground` `finally` only (no extra reconciliation in this change).

PID safety: collector считает process живым только при `psutil.Process(pid).create_time() == recorded_create_time` и `psutil.pid_exists(pid)`; несовпадение → `runtime.state="stopped"` (PID reuse). Каталог-запись остаётся, пока `run_foreground`/reconciliation её не очистит.

#### Scenario: Migration adds runtime table

- **WHEN** catalog at schema v8 is opened
- **THEN** `environment_runtime` table is created, `PRAGMA user_version = 9`, existing environments have no runtime row

#### Scenario: Upsert is one-row-per-environment

- **WHEN** `upsert_environment_runtime(env_id, ...)` is called twice for the same environment
- **THEN** one row exists with the latest values (no duplicates)

#### Scenario: Collector reads runtime read-only

- **WHEN** `EnvironmentMonitor.snapshot()` runs
- **THEN** it calls `list_environment_runtimes()` (read-only); collector never calls `upsert`/`clear`

#### Scenario: PID reuse reconciled as stopped

- **WHEN** catalog has a runtime row for env with PID 43120/create_time T1, but the live PID 43120 has create_time T2
- **THEN** collector reports `runtime.state="stopped"`; the catalog row is left untouched by collector

#### Scenario: Missing runtime row means stopped

- **WHEN** an environment has no row in `environment_runtime`
- **THEN** collector reports `runtime.state="stopped"`, `runtime.root_pid=None`