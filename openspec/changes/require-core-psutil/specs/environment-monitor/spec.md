## MODIFIED Requirements

### Requirement: One `EnvironmentMonitor` collector

SDK MUST предоставлять один public collector primitive в
`odoo_instance_sdk.resources.monitor`:

```python
from odoo_instance_sdk import EnvironmentMonitor

monitor = EnvironmentMonitor()
snapshot = monitor.snapshot(project_id=None)

async for snapshot in monitor.watch(interval=2.0, project_id=None):
    await publish(snapshot)
```

`EnvironmentMonitor` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)`
без обязательных полей (default constructor `EnvironmentMonitor()`); все его
dependencies (catalog, `psutil`, Docker CLI runner, Git CLI) resolved internally
from existing internal helpers. `psutil` is a required core dependency, not an
optional monitor extra. Конфигурируемость — optional keyword fields для injection
в тестах (catalog path, fake process/git/docker providers); default constructor
работает без аргументов.

`snapshot(project_id: str | None = None) -> Snapshot` MUST выполнять один
согласованный сбор и возвращать typed immutable `Snapshot`. Сбор non-blocking по
отношению к foreground Odoo process: collector не запускает, не останавливает и
не шлёт signals процессам, не меняет catalog и не трогает cluster state.

`watch(interval: float = 2.0, project_id: str | None = None) ->
AsyncIterator[Snapshot]` MUST быть thin async generator поверх `snapshot()` и
stdlib `asyncio.sleep(interval)`; без собственного
scheduler/queue/threadpool/background task. Consumer cancellation
(`asyncio.CancelledError`, `break`, generator `aclose`) MUST корректно завершать
`watch()`; collector не оставляет background threads/processes после остановки
consumer task. `interval` MUST быть `>= 0.1`; `interval < 0.1` — `ValueError`.

`EnvironmentMonitor` MUST быть единственным владельцем discovery, reconciliation
и metric computation. FastAPI endpoint, CLI `env list`/`monitor` и React UI
потребляют `EnvironmentMonitor.snapshot()` / `watch()` и MUST NOT дублировать
расчёт metrics. Не добавлять public interfaces/factories/ABC. Internal test
Protocols (`ProcessProvider`, `GitProvider`, `DockerProvider`) allowed only as
optional constructor injection; production path uses the default `None`
implementations.

#### Scenario: Default constructor works

- **WHEN** `EnvironmentMonitor()` is constructed without arguments
- **THEN** catalog path resolves via existing `get_catalog_path()` and core
  `psutil` plus Docker/Git CLIs are resolved by the normal collector path

#### Scenario: Snapshot is typed immutable

- **WHEN** `monitor.snapshot()` returns
- **THEN** returned object is a `Snapshot` `msgspec.Struct` with
  `frozen=True, forbid_unknown_fields=True`; all nested models are frozen
  `msgspec.Struct`

#### Scenario: Watch is cancellable without leaks

- **WHEN** consumer cancels the task iterating `monitor.watch()` mid-iteration
- **THEN** `watch()` stops yielding, no background thread/process survives past
  consumer exit

#### Scenario: Interval floor enforced

- **WHEN** `monitor.watch(interval=0.05)` is called
- **THEN** `ValueError` is raised before any iteration

### Requirement: `EnvironmentSnapshot` runtime states

Каждый non-removed environment MUST становиться одним `EnvironmentSnapshot`.
`runtime.state` MUST быть enum `RuntimeState` со значениями:

- `stopped` — verified process отсутствует (нет runtime-записи, либо
  PID+`create_time` не совпадают, т.е. stale/reused PID);
- `ready` — process жив и bounded Odoo readiness probe успешен;
- `not_ready` — process жив, probe неуспешен.

`stopped` environment остаётся карточкой: Git/storage metadata доступны, Odoo
PID/CPU/RAM `None`, UI "Open Odoo" disabled.

Reconciliation: collector читает catalog `environment_runtime` (если есть),
берёт `root_pid` и `create_time`, проверяет exact
`psutil.Process(pid).create_time() == recorded_create_time` и
`psutil.pid_exists(pid)`. Mismatch → `stopped`; collector does not delete the
catalog row. Approximate wall-clock identity fallback is forbidden.

Readiness after a live PID+create_time match: one
`httpx.get(f"{http_url}/web/health?db_server_status=true", timeout=2.0)`.
`ready` iff HTTP 200 and JSON `status == "pass"`. Any timeout, connect error,
non-200, or missing/non-pass status → `not_ready` with process metrics still
populated. Do not call `wait_ready` / `poll_health` (those poll up to 60s).

#### Scenario: Stopped environment has null runtime metrics

- **WHEN** an environment has no current-runtime record in catalog
- **THEN** `runtime.state == "stopped"`, `runtime.root_pid is None`,
  `runtime.cpu_percent is None`

#### Scenario: PID reuse reconciles as stopped

- **WHEN** an environment has a runtime record with PID 43120 and
  `create_time=T1`, but `psutil.Process(43120).create_time() == T2 != T1`
- **THEN** `runtime.state == "stopped"`, runtime metrics are null; catalog
  record is not deleted by collector

#### Scenario: Exact persisted foreground runtime is live

- **WHEN** a catalog row was persisted from a running environment foreground
  process
- **THEN** the monitor accepts that row as live when the exact PID and
  `create_time` match

#### Scenario: Ready environment shows live metrics

- **WHEN** an environment has a live Odoo process matching PID+`create_time`
  and readiness probe succeeds
- **THEN** `runtime.state == "ready"`, `runtime.root_pid` is the verified PID,
  `runtime.cpu_percent` and `runtime.rss_bytes` are aggregated over the
  process tree

#### Scenario: Live process but probe fails

- **WHEN** an environment has a live matching process but
  `GET {http_url}/web/health?db_server_status=true` does not return HTTP 200
  with JSON `status=="pass"` within 2.0s
- **THEN** `runtime.state == "not_ready"`, process metrics still populated

### Requirement: Component failure isolation

Сбор snapshot MUST не падать целиком из-за одной компоненты:

- Ошибка одного environment (Git/storage/psutil/DB size) → that environment
  stays in the snapshot with nested partials (`git.state=orphan`,
  `storage.complete=False`, `runtime.state=stopped`); no environment-level
  `error` field. Other environments continue.
- Ошибка одного cluster (Docker inspect/stats) → affected cluster получает
  `unavailability_reason`, остальные продолжаются.
- Ошибка project manifest load → `cluster=None` для этого project,
  environments продолжаются.
- Catalog SQLite error → snapshot fails целиком с typed `MonitorError` (это
  единственная unrecoverable ошибка — без catalog нет project discovery);
  collector не сваливается в generic `Exception`.
- Docker CLI missing → только affected compose clusters; не global crash
  (covered above).

Типизированные ошибки в `exceptions.py` (наследники `OdooInstanceSdkError`):
`MonitorError` (base). Component failures изолируются в snapshot
(`complete=False`/`unavailability_reason`), не отдельным exception; catalog
SQLite error → `MonitorError`. `MonitorExtrasMissingError` and any missing
`psutil` extra install hint are not part of the public contract because
`psutil` is core.

#### Scenario: One environment failure isolated

- **WHEN** Git CLI fails for one environment's worktree
- **THEN** that environment's `git.state == "orphan"`,
  `ahead`/`behind`/`diff`/`head_sha` are None, `branch == "unknown"`; other
  environments are unaffected

#### Scenario: Catalog error fails snapshot

- **WHEN** `BackupCatalog` raises `BackupCatalogError` during the aggregate
  environment/runtime read
- **THEN** `snapshot()` raises typed `MonitorError`, not a generic `sqlite3.Error`

#### Scenario: Missing psutil extra actionable hint

- **WHEN** an installation is manually corrupted by removing required core
  `psutil`
- **THEN** that unsupported installation has no `metrics` extra or
  `MonitorExtrasMissingError` compatibility contract
