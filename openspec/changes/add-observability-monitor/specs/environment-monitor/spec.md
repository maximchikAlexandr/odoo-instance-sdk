## Purpose

Read-only observability surface over the existing lifecycle catalog, `PostgresCluster` and Docker CLI: one `EnvironmentMonitor` collector that produces a typed immutable snapshot of all catalog projects, their environments and one nullable project PostgreSQL cluster per project, consumed by Python SDK, headless FastAPI JSON API and a React+Mantine Web UI. No control operations, no historical metrics, no second catalog.

## ADDED Requirements

### Requirement: One `EnvironmentMonitor` collector

SDK MUST предоставлять один public collector primitive в `odoo_instance_sdk.resources.monitor`:

```python
from odoo_instance_sdk import EnvironmentMonitor

monitor = EnvironmentMonitor()
snapshot = monitor.snapshot(project_id=None)

async for snapshot in monitor.watch(interval=2.0, project_id=None):
    await publish(snapshot)
```

`EnvironmentMonitor` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` без обязательных полей (default constructor `EnvironmentMonitor()`); все его dependencies (catalog, `psutil`, Docker CLI runner, Git CLI) resolved internally from existing internal helpers. Конфигурируемость — optional keyword fields для injection в тестах (catalog path, fake process/git/docker providers); default constructor работает без аргументов.

`snapshot(project_id: str | None = None) -> Snapshot` MUST выполнять один согласованный сбор и возвращать typed immutable `Snapshot`. Сбор non-blocking по отношению к foreground Odoo process: collector не запускает, не останавливает и не шлёт signals процессам, не меняет catalog и не трогает cluster state.

`watch(interval: float = 2.0, project_id: str | None = None) -> AsyncIterator[Snapshot]` MUST быть thin async generator поверх `snapshot()` и stdlib `asyncio.sleep(interval)`; без собственного scheduler/queue/threadpool/background task. Consumer cancellation (`asyncio.CancelledError`, `break`, generator `aclose`) MUST корректно завершать `watch()`; collector не оставляет background threads/processes после остановки consumer task. `interval` MUST быть `>= 0.1`; `interval < 0.1` — `ValueError`.

`EnvironmentMonitor` MUST быть единственным владельцем discovery, reconciliation и metric computation. FastAPI endpoint, CLI `env list`/`monitor` и React UI потребляют `EnvironmentMonitor` (или его snapshot models) и MUST NOT дублировать расчёт metrics. Не добавлять interfaces/factories/Protocol для единственной реализации.

#### Scenario: Default constructor works

- **WHEN** `EnvironmentMonitor()` is constructed without arguments
- **THEN** catalog path resolves via existing `get_catalog_path()`, `psutil`/Docker/Git CLIs resolved lazily on first `snapshot()`

#### Scenario: Snapshot is typed immutable

- **WHEN** `monitor.snapshot()` returns
- **THEN** returned object is a `Snapshot` `msgspec.Struct` with `frozen=True, forbid_unknown_fields=True`; all nested models are frozen `msgspec.Struct`

#### Scenario: Watch is cancellable without leaks

- **WHEN** consumer cancels the task iterating `monitor.watch()` mid-iteration
- **THEN** `watch()` stops yielding, no background thread/process survives past consumer exit

#### Scenario: Interval floor enforced

- **WHEN** `monitor.watch(interval=0.05)` is called
- **THEN** `ValueError` is raised before any iteration

### Requirement: Snapshot top-level contract

`Snapshot` MUST быть `msgspec.Struct(frozen=True, forbid_unknown_fields=True, kw_only=True)` с полями:

- `schema_version: int` (always `1`);
- `generated_at: datetime` (tz-aware UTC);
- `projects: tuple[ProjectSummary, ...]`;
- `environments: tuple[EnvironmentSnapshot, ...]`.

`projects` и `environments` — упорядочены стабильно (по `project_id` / `environment id`), не по времени. Backend `GET /api/v1/snapshot?project_id=<opaque>` и CLI `--json` MUST возвращать ровно этот contract (один versioned envelope для monitor; JSON envelope v1 для `env list` остаётся отдельной CLI-specific формой).

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-24T12:00:00Z",
  "projects": [
    {"id": "project_7e3d", "name": "comerta", "environment_count": 3,
     "cluster": {"mode": "compose", "state": "healthy"}}
  ],
  "environments": []
}
```

`project_id` filter: при `project_id=None` возвращаются все проекты/environments; при `project_id=<opaque>` — только один matching project, его environments и его cluster; unknown `project_id` — пустые tuples (не error), чтобы polling не падал при race между stale project и concurrent removal.

#### Scenario: Full snapshot shape

- **WHEN** `monitor.snapshot()` runs with two projects each having environments
- **THEN** `projects` contains two `ProjectSummary`, `environments` contains all non-removed environments of both projects

#### Scenario: Project filter narrows result

- **WHEN** `monitor.snapshot(project_id="project_7e3d")` runs
- **THEN** `projects` contains only the matching `ProjectSummary` and `environments` contains only that project's environments

#### Scenario: Unknown project filter returns empty

- **WHEN** `monitor.snapshot(project_id="project_unknown")` runs
- **THEN** `projects == ()` and `environments == ()`, no exception

### Requirement: Project discovery from canonical repository provenance

Project identity MUST строиться из canonical repository provenance существующих catalog environments, **не** из process registry и **не** из filesystem scan.

- Источник списка — `BackupCatalog.list_environments(include_removed=False)`; все environments с `state != "removed"` включаются.
- Группировка по `git_common_dir` (canonical Git common dir, уже хранится в catalog), не по display name и не по `repository_root`.
- `project_id` — стабильный opaque identifier, вычисляемый детерминированно из `git_common_dir` как `repo_key(repository_root, git_common_dir)` (существующий helper), с prefix `project_` (например `project_comerta_7e3d`); одинаковый между запусками на одном catalog.
- `name` — короткое project name из `repository_root` basename (без абсолютного пути); одинаковые имена репозиториев различаются по дополнительному short hash-хинту, выведенному из `project_id`, **без раскрытия абсолютного local path**.
- Глобальный режим (`project_id=None`) не сканирует filesystem в поисках незарегистрированных repositories.
- Project, явно переданный через CLI/SDK context (через `project_id`), отображается даже если у него ноль environments в catalog (фильтр по `project_id` возвращает project summary с `environment_count=0` и `environments=()`); это не требует отдельного "registered projects" registry.

```python
ProjectSummary(
    id="project_comerta_7e3d",
    name="comerta",
    display_hint="comerta_7e3d",  # disambiguator, no absolute path
    environment_count=3,
    cluster: ClusterSnapshot | None,
)
```

#### Scenario: Projects grouped by canonical provenance

- **WHEN** catalog contains environments from two repositories both named "odoo" but with different `git_common_dir`
- **THEN** two distinct `ProjectSummary` entries with different `project_id` and disambiguated `display_hint`

#### Scenario: Stopped environment still in project

- **WHEN** an environment has `state="ready"` but no running Odoo process
- **THEN** it still contributes to its project's `environment_count` and appears in `environments`

#### Scenario: Removed environment excluded

- **WHEN** an environment has `state="removed"` in catalog
- **THEN** it is excluded from `projects` and `environments` (include_removed is always False for monitor)

#### Scenario: Project filter with zero environments

- **WHEN** `monitor.snapshot(project_id="project_x")` matches a project that has no environments in catalog
- **THEN** `projects` contains that `ProjectSummary` with `environment_count=0`, `environments == ()`

### Requirement: One nullable project cluster snapshot

Каждый displayed project MUST содержать ровно один nullable `ClusterSnapshot` в `ProjectSummary.cluster`. Cluster — общая project-level зависимость, MUST NOT дублироваться внутри environment rows.

- Для project, чьи environments ссылаются на repository с `[postgres] mode="compose"` manifest, `cluster` — non-null `ClusterSnapshot` (даже если контейнер stopped/missing — с соответствующим `state`/error).
- Для project с `[postgres] mode="external"` (или legacy без секции) `cluster` — non-null `ClusterSnapshot` с `mode="external"`, `owned=False`, container fields `null` и `unavailability_reason="external_not_owned"`.
- Для project без доступного manifest (например orphan environment с удалённым repository root) `cluster` — `None` (явный null в JSON); collector не падает.
- Container CPU/RAM/volume cluster metrics не приписываются ни одному environment и не делятся поровну между worktrees; они живут только на cluster card / project-level CLI summary.

#### Scenario: Compose project has cluster card

- **WHEN** a project has a compose manifest and a healthy container
- **THEN** `ProjectSummary.cluster` is a non-null `ClusterSnapshot` with `mode="compose"`, `owned=True`, non-null container/metrics

#### Scenario: External project has cluster card with null container

- **WHEN** a project has an external manifest
- **THEN** `ProjectSummary.cluster` is non-null with `mode="external"`, `owned=False`, `container is None` and `unavailability_reason="external_not_owned"`

#### Scenario: Missing manifest yields null cluster

- **WHEN** a project's repository root is unreadable and manifest cannot be loaded
- **THEN** `ProjectSummary.cluster is None` and snapshot still returns

### Requirement: `EnvironmentSnapshot` runtime states

Каждый non-removed environment MUST становиться одним `EnvironmentSnapshot`. `runtime.state` MUST быть enum `RuntimeState` со значениями:

- `stopped` — verified process отсутствует (нет runtime-записи, либо PID+`create_time` не совпадают, т.е. stale/reused PID);
- `ready` — process жив и bounded Odoo readiness probe успешен;
- `not_ready` — process жив, probe неуспешен.

`stopped` environment остаётся карточкой: Git/storage metadata доступны, Odoo PID/CPU/RAM отсутствуют (`null`), `Open Odoo` disabled.

Reconciliation: collector читает catalog current-runtime record (если есть), берёт `root_pid` и `create_time`, проверяет через `psutil.Process(pid).create_time() == recorded_create_time` и `psutil.pid_exists(pid)`; при несовпадении runtime state = `stopped` (PID reuse) и stale record игнорируется для snapshot (но НЕ удаляется из catalog — очистку делает `run_foreground` при следующем реальном spawn или отдельная reconciliation; cleanup out of scope для MVP).

#### Scenario: Stopped environment has null runtime metrics

- **WHEN** an environment has no current-runtime record in catalog
- **THEN** `runtime.state == "stopped"`, `runtime.root_pid is None`, `runtime.cpu_percent is None`

#### Scenario: PID reuse reconciles as stopped

- **WHEN** an environment has a runtime record with PID 43120 and `create_time=T1`, but `psutil.Process(43120).create_time() == T2 != T1`
- **THEN** `runtime.state == "stopped"`, runtime metrics are null; catalog record is not deleted by collector

#### Scenario: Ready environment shows live metrics

- **WHEN** an environment has a live Odoo process matching PID+`create_time` and readiness probe succeeds
- **THEN** `runtime.state == "ready"`, `runtime.root_pid` is the verified PID, `runtime.cpu_percent` and `runtime.rss_bytes` are aggregated over the process tree

#### Scenario: Live process but probe fails

- **WHEN** an environment has a live matching process but the readiness HTTP probe fails
- **THEN** `runtime.state == "not_ready"`, process metrics still populated

### Requirement: Odoo process tree metrics

Для живого Odoo process tree (`root + recursive children/workers`) `RuntimeMetrics` MUST содержать:

- `state: RuntimeState`;
- `root_pid: int | None` — persisted/verified Odoo PID;
- `child_pids: tuple[int, ...]` — текущие recursive child/worker PIDs, собираемые live и не сохраняемые в catalog;
- `process_count: int` — root + доступные children (`1 + len(child_pids)`);
- `cpu_percent: float | None` — сумма top-like non-blocking CPU; может превышать `100%`; первый CPU sample MAY быть `null` (psutil требует два замера с интервалом; collector хранит предыдущую CPU-точку в памяти по `(pid, create_time)`);
- `rss_bytes: int | None` — сумма RSS;
- `started_at: datetime | None` — из runtime record;
- `http_url: str | None` — полный Odoo HTTP URL вида `http://<http_interface>:<http_port>` (construct from `StartConfig.http_interface`+`http_port` в `run_foreground`; UI "Open Odoo" открывает этот URL напрямую);
- `http_port: int | None`, `database_name: str | None` — из runtime record;
- `commit_sha: str | None`, `branch: str | None` — из runtime record.

`NoSuchProcess`, `AccessDenied`, `ZombieProcess` изолируются на уровне одного environment: affected environment получает `runtime.state="stopped"` (или `not_ready` если процесс был жив на старте snapshot, но исчез mid-aggregation) и `null` resource fields; snapshot продолжает собираться.

CPU sample interval — bounded (например `psutil` default interval при втором замере); collector не блокируется надолго на одном environment. Историю метрик не сохранять.

#### Scenario: Aggregated CPU over tree

- **WHEN** a ready Odoo root PID 43120 has two workers 43131, 43132
- **THEN** `process_count == 3`, `child_pids == (43131, 43132)`, `cpu_percent` is the sum across the three PIDs

#### Scenario: First sample CPU is null

- **WHEN** a snapshot is the first after process start and no previous CPU point exists
- **THEN** `cpu_percent is None` (one-shot snapshot has no delta); a subsequent `watch()` iteration produces a numeric `cpu_percent`

#### Scenario: AccessDenied isolated

- **WHEN** `psutil.Process(43120)` raises `AccessDenied` for one environment
- **THEN** that environment's `runtime` reflects the error (`state="not_ready"` or `stopped`, metrics null) and other environments in the snapshot are unaffected

### Requirement: Git activity relative to default branch

`GitActivity` MUST содержать (семантика `wt list` из Worktrunk):

- `default_branch: str` — resolved default branch name;
- `head_sha: str | None` — полный HEAD SHA;
- `short_sha: str | None` — short SHA (>=7 chars);
- `branch: str` — текущая checkout branch;
- `ahead: int | None` — commits ahead относительно default branch tip;
- `behind: int | None` — commits behind;
- `diff: GitDiff | None` — `{added: int, deleted: int}` добавленных/удалённых текстовых строк в three-dot diff от merge-base;
- `state: GitActivityState` — `clean | ahead | behind | diverged | orphan`.

Правила:
- default branch tip — upstream tip, если доступен (`git rev-parse --verify <default>@{upstream}` или эквивалент); fallback — локальная default branch;
- line totals относятся к committed three-dot diff (`git diff <merge-base>...HEAD`) и не смешиваются с uncommitted `HEAD±`;
- binary files не дают фиктивных line counts (пропускаются);
- no-common-ancestor → `state="orphan"`, `ahead/behind/diff` — `None`, snapshot не ломается;
- кешировать Git activity по `(worktree_path, HEAD SHA, default-branch SHA)` с bounded TTL (15s), отделённым от CPU/RAM polling.

`default_branch` определяется через project manifest (если есть поле) или fallback `main` (если manifest не указан); fallback выбран явно и стабилен, не гадается из remote refs.

#### Scenario: Clean branch

- **WHEN** HEAD equals default branch tip with no divergence
- **THEN** `state="clean"`, `ahead=0`, `behind=0`, `diff={added:0,deleted:0}`

#### Scenario: Diverged with line counts

- **WHEN** HEAD is 4 ahead and 1 behind default branch tip
- **THEN** `state="diverged"`, `ahead=4`, `behind=1`, `diff` reports non-zero added/deleted text lines

#### Scenario: Orphan no common ancestor

- **WHEN** HEAD has no common ancestor with default branch
- **THEN** `state="orphan"`, `ahead is None`, `behind is None`, `diff is None`

#### Scenario: Binary files skipped

- **WHEN** the three-dot diff contains a binary file
- **THEN** that file contributes `0` to `diff.added`/`diff.deleted` (no spurious line count)

#### Scenario: Stale local main falls back to upstream

- **WHEN** local `main` is behind upstream `origin/main` and upstream is reachable
- **THEN** ahead/behind computed against upstream tip, not stale local `main`

### Requirement: Environment disk footprint

`StorageFootprint` MUST следовать единой формуле:

```text
total = worktree
      + owned Python environment
      + owned database footprint
      + other environment files
```

- `total_bytes: int`;
- `complete: bool` — `true` если все owned components удалось измерить, `false` если хотя бы один недоступен (известная сумма отображается с `>=` в UI);
- `worktree_bytes: int | None`;
- `python_environment: PythonEnvFootprint` (`owned: bool`, `bytes: int | None`); reused/external venv — `owned=False` и не включается в total;
- `database: DatabaseFootprint | None` (`owned: bool`, `postgres_bytes: int | None`, `filestore_bytes: int | None`, `total_bytes: int | None`); только journal-owned `target_db` в `copy` mode; `shared` mode → `owned=False`, `bytes=None`, не включается;
- `other_files_bytes: int | None` — generated config, dependency lock, local logs/cache/artifacts и остальные файлы environment root, не вошедшие выше.

Ownership rules:
- worktree — filesystem size environment worktree;
- Python environment — только при `python_environment_owned = true`;
- database — только journal-owned `target_db` в `copy` mode: PostgreSQL logical size через read-only `pg_database_size(target_db)` (не общий cluster/volume) + соответствующий Odoo filestore после существующих containment checks; будущий dedicated owned DB volume заменяет PostgreSQL logical size, но не суммируется с ним;
- other files — generated config/lock/local logs/cache/artifacts и остальные файлы environment root, не вошедшие выше.

Shared/source database, внешний venv, общий Git object store и общий PostgreSQL cluster **не** учитываются. `pg_database_size(target_db)` — environment-level logical metric; physical managed PostgreSQL volume показывается только на project cluster card и не суммируется в environment disk total (иначе один shared volume посчитан для каждого worktree).

Filesystem scan не следует по symlinks за owned roots, исключает nested component roots и не считает inode/path дважды (дедупликация по realpath). При недоступном owned component `complete=False`.

Storage имеет отдельный bounded cache (15s), отделённый от Odoo CPU/RAM polling и от Docker stats.

#### Scenario: Owned venv included

- **WHEN** an environment has `python_environment_owned=true` with a 700 MiB venv
- **THEN** `python_environment.owned=True`, `python_environment.bytes=734_003_200` (approx), included in `total_bytes`

#### Scenario: Reused venv excluded

- **WHEN** an environment has `python_environment_owned=false`
- **THEN** `python_environment.owned=False`, `python_environment.bytes=None`, venv NOT included in `total_bytes`

#### Scenario: Shared DB excluded from total

- **WHEN** an environment has `db_mode="shared"`
- **THEN** `database.owned=False`, `database.postgres_bytes=None`, `database.filestore_bytes=None`, database NOT included in `total_bytes`

#### Scenario: Copy DB included

- **WHEN** an environment has `db_mode="copy"` with owned target DB
- **THEN** `database.owned=True`, `database.postgres_bytes` from `pg_database_size(target_db)`, `database.filestore_bytes` from contained filestore, included in `total_bytes`

#### Scenario: Incomplete measurement flagged

- **WHEN** owned worktree is measurable but owned DB is unreachable
- **THEN** `complete=False`, known sum still returned (UI shows `≥`)

#### Scenario: Symlink not followed into shared store

- **WHEN** worktree contains a symlink pointing into the shared Git object store
- **THEN** the symlink target is not traversed; not counted in `total_bytes`

#### Scenario: Shared cluster volume not in environment total

- **WHEN** a project has a compose cluster with a 12 GiB managed volume
- **THEN** that volume appears only on `ClusterSnapshot.metrics.volume_usage_bytes`, NOT summed into any environment `total_bytes`

### Requirement: `ClusterSnapshot` container identity and resources

`ClusterSnapshot` MUST содержать:

- `mode: Literal["external", "compose"]`;
- `owned: bool`;
- `state: PostgresClusterState` (existing enum: `unknown|unreachable|starting|healthy|stopped|unhealthy`);
- `endpoint: ClusterEndpoint | None` (`host: str`, `port: int`) — redacted, loopback-only для compose;
- `container: ClusterContainer | None`;
- `metrics: ClusterMetrics | None`;
- `unavailability_reason: str | None` — stable reason code (`external_not_owned`, `stopped`, `missing`, `docker_unavailable`, `inspect_failed`, `stats_failed`);
- `sampled_at: datetime | None`.

`ClusterContainer`:
- `id: str | None` — short container ID (12 hex, redacted prefix);
- `name: str | None`;
- `image: str | None`;
- `pid: int | None` — Docker-reported init PID;
- `pid_scope: Literal["host", "docker_vm", "unavailable"]` — `host` на native Linux (Docker daemon host PID namespace), `docker_vm` на macOS Docker Desktop/Colima (PID в Linux VM, не macOS PID), `unavailable` для stopped/missing/external. Known limitation: Docker Desktop on Linux также запускает Docker в VM, но detection (`sys.platform`) помечает его как `host`; это edge case issue #11 не требует (issue говорит "host на native Linux"), оставляем `host` с documented limitation — future refinement out of scope.

`ClusterMetrics`:
- `cpu_percent: float | None` — Docker-reported container CPU percent;
- `memory_usage_bytes: int | None`;
- `memory_limit_bytes: int | None`;
- `volume_usage_bytes: int | None` — managed volume usage, только если Docker предоставляет его без privileged host traversal; иначе `null`;
- `sampled_at: datetime | None`.

Правила:
- Compose cluster: container resolved через recorded project provenance + deterministic Compose project name (`odcli_pg_<project-id>`, существующий) + service identity; затем read-only `docker inspect`/`docker stats --no-stream` (не `psutil` — одинаково работает на Linux и Docker VM).
- External cluster: `mode="external"`, `owned=False`, `endpoint` из source config (redacted), `container=None`, `metrics=None`, `unavailability_reason="external_not_owned"`; SDK не пытается находить или инспектировать произвольный PostgreSQL process.
- Stopped/missing compose cluster: `container=None`, `metrics=None`, `unavailability_reason="stopped"` (или `"missing"` если контейнер отсутствует даже после `compose ps`).
- Docker unavailable: `container=None`, `metrics=None`, `unavailability_reason="docker_unavailable"`.
- Individual PostgreSQL backend PIDs клиентских соединений не отображаются: они короткоживущие и не представляют identity cluster runtime. Container init PID — identity cluster runtime.
- Ошибка `docker inspect`/`stats` для одного project не роняет весь snapshot: affected cluster получает `unavailability_reason="inspect_failed"`/`"stats_failed"`, остальные продолжаются.

Для нескольких managed projects container IDs собираются через Compose provenance, затем `docker inspect`/`docker stats --no-stream` выполняются **batch**-вызовами (один `docker stats --no-stream <id1> <id2> ...` или один `docker inspect <id1> <id2> ...`), а не отдельным subprocess на каждую карточку. Один `docker stats` call может не вернуть данные по одному контейнеру — он не блокирует остальные; для отсутствующего контейнера collector отдельно не ретраит (next polling tick соберёт заново).

#### Scenario: Compose healthy with container metrics

- **WHEN** a compose cluster is healthy with a running container
- **THEN** `container.id` (12 hex), `container.name`, `container.image`, `container.pid` (int), `pid_scope` ∈ {`host`,`docker_vm`}, `metrics.cpu_percent`/`memory_usage_bytes`/`memory_limit_bytes` populated

#### Scenario: Linux host PID scope

- **WHEN** running on native Linux with Docker daemon using host PID namespace
- **THEN** `pid_scope="host"`, `pid` is a host PID

#### Scenario: macOS Docker VM PID scope

- **WHEN** running on macOS with Docker Desktop or Colima
- **THEN** `pid_scope="docker_vm"`, `pid` is a Linux-VM PID and NOT labeled as a macOS PID

#### Scenario: External cluster not inspected

- **WHEN** a project has `[postgres] mode="external"`
- **THEN** `container is None`, `metrics is None`, `unavailability_reason="external_not_owned"`, no Docker invocation

#### Scenario: Stopped compose has null container

- **WHEN** a compose cluster is stopped (`state="stopped"`)
- **THEN** `container is None`, `metrics is None`, `unavailability_reason="stopped"`

#### Scenario: Docker unavailable does not crash snapshot

- **WHEN** `docker` is not in PATH and a compose project exists
- **THEN** that project's `cluster.unavailability_reason="docker_unavailable"`, other projects/environments still in snapshot

#### Scenario: Batch stats for multiple containers

- **WHEN** two managed projects have healthy containers
- **THEN** collector issues a bounded batch `docker stats --no-stream <id1> <id2>` rather than one subprocess per container

#### Scenario: One container stats failure isolated

- **WHEN** batch `docker stats` returns data for one container but errors for another
- **THEN** the failed container's cluster gets `unavailability_reason="stats_failed"`, the succeeded one gets metrics; snapshot not crashed

### Requirement: Bounded caching separates expensive and cheap sections

Collector MUST разделять кеширование:

- Odoo CPU/RAM polling (process tree) — **без** кеша (каждая `snapshot()`/`watch()` итерация свежая; first CPU sample может быть `null`);
- expensive sections — bounded TTL cache (default 15s), key по стабильному identity:
  - Git activity — `(worktree_path, HEAD SHA, default-branch SHA)`;
  - Storage footprint — `environment_id` (stable catalog PK);
  - Docker container identity (`docker inspect`) — `container_id`;
  - Docker stats — `container_id`;
- cluster `state` (read-only `PostgresCluster.status()`) — bounded TTL cache (5s), чтобы не вызывать Docker/`pg_isready` на каждом 2s tick.

Кеш живёт в памяти одного `EnvironmentMonitor` instance и не персистится. `EnvironmentMonitor()` новый instance — пустой кеш. TTL истёк → пересчёт на следующем `snapshot()`. Cache miss не блокирует: collector считает синхронно в рамках `snapshot()` (этого достаточно для MVP; async background refresh out of scope).

#### Scenario: CPU not cached

- **WHEN** `snapshot()` is called twice in rapid succession (< 2s)
- **THEN** both calls compute fresh `cpu_percent` deltas (no cached CPU value reused)

#### Scenario: Git activity cached within TTL

- **WHEN** `snapshot()` is called twice within 15s for the same environment with unchanged HEAD
- **THEN** Git activity is computed once and reused on the second call

#### Scenario: Storage cached within TTL

- **WHEN** `watch(interval=2.0)` iterates 5 times within 15s
- **THEN** storage footprint is computed once, reused across the 5 iterations

#### Scenario: Fresh monitor instance empties cache

- **WHEN** a second `EnvironmentMonitor()` instance is created and `snapshot()` is called
- **THEN** caches are empty and all expensive sections are recomputed

### Requirement: Component failure isolation

Сбор snapshot MUST не падать целиком из-за одной компоненты:

- Ошибка одного environment (Git/storage/psutil/DB size) → affected environment получает partial snapshot (`complete=False`, error field), остальные environments продолжаются.
- Ошибка одного cluster (Docker inspect/stats) → affected cluster получает `unavailability_reason`, остальные продолжаются.
- Ошибка project manifest load → `cluster=None` для этого project, environments продолжаются.
- Catalog SQLite error → snapshot fails целиком с typed `MonitorError` (это единственная unrecoverable ошибка — без catalog нет project discovery); collector не сваливается в generic `Exception`.
- `psutil` import error (missing extra) → `EnvironmentMonitor()` construction или first `snapshot()` raises typed `MonitorExtrasMissingError` с actionable install hint (`pip install odoo-instance-sdk[metrics]`); не падает в generic `ImportError`.
- Docker CLI missing → только affected compose clusters; не global crash (covered above).

Типизированные ошибки в `exceptions.py` (наследники `OdooInstanceSdkError`): `MonitorError` (base), `MonitorExtrasMissingError`. Сообщения redacted (без secrets/absolute paths). Component failures изолируются в snapshot (`complete=False`/`unavailability_reason`), не отдельным exception; catalog SQLite error → `MonitorError`.

#### Scenario: One environment failure isolated

- **WHEN** Git CLI fails for one environment's worktree
- **THEN** that environment's `git` reflects error (`state="orphan"` or null fields with `complete=False`), other environments in snapshot unaffected

#### Scenario: Catalog error fails snapshot

- **WHEN** `BackupCatalog` raises `BackupCatalogError` during `list_environments()`
- **THEN** `snapshot()` raises typed `MonitorError`, not a generic `sqlite3.Error`

#### Scenario: Missing psutil extra actionable hint

- **WHEN** `psutil` is not installed and `EnvironmentMonitor().snapshot()` is called
- **THEN** `MonitorExtrasMissingError` with message containing `pip install odoo-instance-sdk[metrics]`

### Requirement: Snapshot redaction and no secrets

Snapshot (SDK/API/CLI/JSON) MUST NOT содержать:

- credentials, passwords, secret file content, environment variables;
- command line (`psutil.Process.cmdline()`) и args Odoo process;
- абсолютные local repository/worktree/venv paths (отображаются только opaque `project_id`, environment `name`, branch, short SHA; `worktree_path`/`python_environment_path` НЕ в snapshot — они catalog-internal);
- raw Docker inspect payload (только redacted fields выше).

Endpoint cluster — loopback-only для compose; для external `host` из source config, но без password. `db_password`/`POSTGRES_PASSWORD_FILE` — никогда. Existing `StartConfig.__repr__`/`PostgresCluster.__repr__` redaction сохраняется.

#### Scenario: No absolute path in snapshot JSON

- **WHEN** `GET /api/v1/snapshot` returns JSON
- **THEN** no field contains an absolute local path (worktree/venv/repository_root); only opaque `project_id`, `name`, `branch`, `short_sha`

#### Scenario: No command line in runtime

- **WHEN** a ready environment snapshot is serialised
- **THEN** `runtime` contains PIDs and resource metrics but no `cmdline`/`args`

#### Scenario: No Docker password in container

- **WHEN** `docker inspect` returns env vars including `POSTGRES_PASSWORD_FILE`
- **THEN** snapshot `container`/`metrics` expose only the redacted fields; no secret values

### Requirement: `msgspec` typed models, no Pydantic DTO duplication

Snapshot models MUST быть frozen `msgspec.Struct(frozen=True, forbid_unknown_fields=True, kw_only=True)` — reusing принятый в SDK `msgspec`, не дублируются Pydantic DTO. FastAPI использует `msgspec` для JSON encode (через `msgspec.json.encode` или существующий pattern); Pydantic не добавляется как dependency. Все enums (`RuntimeState`, `GitActivityState`) — `enum.StrEnum`.

Приложение может использовать collector внутри своего FastAPI/Flask/worker process без зависимости от встроенного FastAPI backend (extra `metrics` достаточно для SDK; `dashboard` только для built-in server).

#### Scenario: Models are msgspec Structs

- **WHEN** source is inspected for `Snapshot`/`EnvironmentSnapshot`/`ClusterSnapshot`
- **THEN** all are `msgspec.Struct` subclasses with `frozen=True, forbid_unknown_fields=True, kw_only=True`

#### Scenario: FastAPI without Pydantic

- **WHEN** the built-in FastAPI server serialises a snapshot
- **THEN** it uses `msgspec.json.encode` (or equivalent), not Pydantic; `pydantic` is not a runtime dependency

### Requirement: No second catalog, no docker-py, no generic provider, no event bus

Collector MUST переиспользовать существующие primitives и MUST NOT добавлять:

- второй SQLite catalog (используется `BackupCatalog` через existing `get_catalog_path()`);
- docker-py (используется установленный Docker CLI через existing Compose runner / `subprocess`);
- generic provider/plugin architecture или interfaces/factories для единственной реализации;
- event bus, persistent metrics daemon, background sampler process;
- второй process registry (используется catalog current-runtime record + in-memory `psutil`).

`EnvironmentMonitor` — единственная реализация; не оборачивать в Protocol/ABC с одним impl.

#### Scenario: No second catalog created

- **WHEN** `EnvironmentMonitor().snapshot()` runs
- **THEN** it reads the existing `get_catalog_path()` catalog; no new SQLite file is created

#### Scenario: No docker-py imported

- **WHEN** the package is inspected for docker-py usage
- **THEN** no `import docker` exists; Docker access is via `subprocess` through the existing Compose runner

#### Scenario: No background sampler

- **WHEN** `watch()` is not being iterated
- **THEN** no background thread/process is alive; `snapshot()` is the only thing that computes