## Purpose

Read-only observability surface over the existing lifecycle catalog, `PostgresCluster` and Docker CLI: one `EnvironmentMonitor` collector that produces a typed immutable snapshot of all catalog projects, their environments and one nullable project PostgreSQL cluster per project, consumed by Python SDK, headless FastAPI JSON API and a React+Mantine Web UI. No control operations, no historical metrics, no second catalog.
## Requirements
### Requirement: Canonical snapshot types

Public snapshot types MUST live in `models.py` as `msgspec.Struct(frozen=True, forbid_unknown_fields=True, kw_only=True)` except StrEnums. `ProcessTreeResult` MUST NOT be public. Field lists below are complete; do not add extra public fields.

```python
class RuntimeState(enum.StrEnum):
    STOPPED = "stopped"
    READY = "ready"
    NOT_READY = "not_ready"

class GitActivityState(enum.StrEnum):
    CLEAN = "clean"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    ORPHAN = "orphan"

class PidScope(enum.StrEnum):
    HOST = "host"
    DOCKER_VM = "docker_vm"
    UNAVAILABLE = "unavailable"

class PortObservation(enum.StrEnum):
    FREE = "free"
    OCCUPIED = "occupied"
    UNKNOWN = "unknown"

class PgAdminEligibilityState(enum.StrEnum):
    ELIGIBLE = "eligible"
    ENVIRONMENT_NOT_READY = "environment_not_ready"
    DATABASE_UNRESOLVED = "database_unresolved"
    CLUSTER_NOT_OWNED = "cluster_not_owned"
    CLUSTER_UNHEALTHY = "cluster_unhealthy"

class PgAdminEligibility:
    state: PgAdminEligibilityState

class GitDiff:
    added: int
    deleted: int

class GitActivity:
    default_branch: str
    head_sha: str | None
    short_sha: str | None
    branch: str
    ahead: int | None
    behind: int | None
    diff: GitDiff | None
    state: GitActivityState

class PythonEnvFootprint:
    owned: bool
    bytes: int | None

class DatabaseFootprint:
    owned: bool
    postgres_bytes: int | None
    filestore_bytes: int | None
    total_bytes: int | None

class StorageFootprint:
    total_bytes: int
    complete: bool
    worktree_bytes: int | None
    python_environment: PythonEnvFootprint
    database: DatabaseFootprint
    other_files_bytes: int | None

class RuntimeMetrics:
    state: RuntimeState
    root_pid: int | None
    child_pids: tuple[int, ...]
    process_count: int
    cpu_percent: float | None
    rss_bytes: int | None
    started_at: datetime | None
    http_url: str | None
    http_port: int | None
    database_name: str | None
    commit_sha: str | None
    branch: str | None

class ClusterContainer:
    id: str | None
    name: str | None
    image: str | None
    pid: int | None
    pid_scope: PidScope

class ClusterMetrics:
    cpu_percent: float | None
    memory_usage_bytes: int | None
    memory_limit_bytes: int | None
    volume_usage_bytes: int | None
    sampled_at: datetime | None

class ClusterEndpoint:
    host: str
    port: int

class ClusterResourceSnapshot:
    container: ClusterContainer | None
    metrics: ClusterMetrics | None
    unavailability_reason: str | None
    sampled_at: datetime | None

class ClusterSnapshot:
    mode: Literal["external", "compose"]
    owned: bool
    state: PostgresClusterState
    endpoint: ClusterEndpoint | None
    container: ClusterContainer | None
    metrics: ClusterMetrics | None
    unavailability_reason: str | None
    sampled_at: datetime | None

class EnvironmentArtifacts:
    worktree_exists: bool
    worktree_registered: bool
    config_exists: bool
    python_exists: bool
    python_contained: bool
    dependency_lock_exists: bool
    backup_exists: bool | None

class EnvironmentSnapshot:
    id: str
    project_id: str
    name: str
    branch: str
    short_sha: str | None
    db_mode: Literal["shared", "copy"]
    database: str | None
    lifecycle_state: EnvironmentState
    allocated_http_port: int | None
    observed_port: PortObservation | None
    artifacts: EnvironmentArtifacts
    runtime: RuntimeMetrics
    git: GitActivity
    storage: StorageFootprint
    pgadmin: PgAdminEligibility

class ProjectSummary:
    id: str
    name: str
    display_hint: str
    environment_count: int
    cluster: ClusterSnapshot | None

class Snapshot:
    schema_version: int
    generated_at: datetime
    projects: tuple[ProjectSummary, ...]
    environments: tuple[EnvironmentSnapshot, ...]
```

`unavailability_reason` allowed values: `external_not_owned`, `stopped`, `missing`, `docker_unavailable`, `inspect_failed`, `stats_failed`.

Collector MUST populate `EnvironmentSnapshot` as:

- `id` / `name` / `db_mode` / `lifecycle_state` — catalog row (`lifecycle_state` is existing `EnvironmentState`: `creating|ready|failed|removing|cleanup_failed|removed`);
- `branch` — catalog `branch` (not `GitActivity.branch`, not runtime record);
- `short_sha` — first 7 hex chars of `git.head_sha`, or `None` if `head_sha` is None;
- `database` — `target_db_name` when `db_mode=="copy"`, else `source_db_name`;
- `allocated_http_port` — `StartConfig.from_odoo_config(generated_config_path).http_port`, or `None` if that file is missing/unreadable. Independent of runtime liveness;
- `observed_port` / `artifacts` — unchanged from the mandatory MYL-55 v2 canonical artifact and port reconciliation requirement;
- `runtime` / `git` / `storage` — as their own requirements;
- `pgadmin.state` — `eligible` only when lifecycle state is `ready`, database is non-null, and the owning project cluster is Compose, SDK-owned, and healthy; otherwise the first applicable exact state in this precedence: `environment_not_ready`, `database_unresolved`, `cluster_not_owned`, `cluster_unhealthy`.

UI lifecycle badge uses `lifecycle_state`. UI/CLI port uses `runtime.http_port` when `runtime.state` is `ready` or `not_ready`, else `allocated_http_port`. UI MUST use `pgadmin.state` directly and MUST NOT recompute eligibility from other fields.

#### Scenario: EnvironmentSnapshot mapping

- **WHEN** a copy-mode catalog row has `branch="feat/x"`, generated config `http_port=8070`, `target_db_name="db_x"`, lifecycle state `ready`, healthy owned Compose cluster, and git `head_sha` starting `abc1234def`
- **THEN** `branch=="feat/x"`, `allocated_http_port==8070`, `database=="db_x"`, `short_sha=="abc1234"`, `lifecycle_state` equals catalog `state`, required typed `observed_port`/`artifacts` retain MYL-55 semantics, and `pgadmin.state=="eligible"`

#### Scenario: EnvironmentSnapshot carries project_id

- **WHEN** `monitor.snapshot()` returns an environment
- **THEN** `environment.project_id` equals the owning `ProjectSummary.id`

#### Scenario: Eligibility precedence is deterministic

- **WHEN** an environment is non-ready and also lacks a database on an external cluster
- **THEN** `environment.pgadmin.state=="environment_not_ready"`

### Requirement: One `EnvironmentMonitor` collector

SDK MUST предоставлять один public collector primitive в `odoo_instance_sdk.resources.monitor`:

```python
from odoo_instance_sdk import EnvironmentMonitor

monitor = EnvironmentMonitor()
snapshot = monitor.snapshot(project_id=None, include_removed=False)

async for snapshot in monitor.watch(
    interval=2.0,
    project_id=None,
    include_removed=False,
):
    await publish(snapshot)
```

`EnvironmentMonitor` MUST быть `@dataclass(frozen=True, slots=True, kw_only=True)` без обязательных полей (default constructor `EnvironmentMonitor()`); все его dependencies (catalog, `psutil`, Docker CLI runner, Git CLI) resolved internally from existing internal helpers. Конфигурируемость — optional keyword fields для injection в тестах (catalog path, fake process/git/docker providers); default constructor работает без аргументов.

`snapshot(project_id: str | None = None, *, include_removed: bool = False) -> Snapshot` MUST выполнять один согласованный сбор и возвращать typed immutable `Snapshot`. It SHALL own catalog discovery, removed-row selection, backup/artifact reconciliation, port observation, project grouping, runtime/PID/resource metrics, Git activity, and storage footprint. Сбор non-blocking по отношению к foreground Odoo process: collector не запускает, не останавливает и не шлёт signals процессам, не меняет catalog и не трогает cluster state.

`watch(interval: float = 2.0, project_id: str | None = None, *, include_removed: bool = False) -> AsyncIterator[Snapshot]` MUST быть thin async generator поверх `snapshot(project_id=..., include_removed=...)` и stdlib `asyncio.sleep(interval)`; без собственного scheduler/queue/threadpool/background task. Consumer cancellation (`asyncio.CancelledError`, `break`, generator `aclose`) MUST корректно завершать `watch()`; collector не оставляет background threads/processes после остановки consumer task. `interval` MUST быть `>= 0.1`; `interval < 0.1` — `ValueError`.

`EnvironmentMonitor` MUST быть единственным владельцем discovery, reconciliation и metric computation. FastAPI endpoint, CLI `env list`/`monitor` и React UI потребляют `EnvironmentMonitor.snapshot()` / `watch()` и MUST NOT дублировать расчёт или сбор. Не добавлять public interfaces/factories/ABC or `CliEnvironmentSnapshot`. Internal test Protocols (`ProcessProvider`, `GitProvider`, `DockerProvider`) allowed only as optional constructor injection; production path uses the default `None` implementations.

#### Scenario: Default constructor works

- **WHEN** `EnvironmentMonitor()` is constructed without arguments
- **THEN** catalog path resolves via existing `get_catalog_path()`, `psutil`/Docker/Git CLIs resolved lazily on first `snapshot()`

#### Scenario: Snapshot is typed immutable

- **WHEN** `monitor.snapshot()` returns
- **THEN** returned object is a `Snapshot` `msgspec.Struct` with `frozen=True, forbid_unknown_fields=True`; all nested models are frozen `msgspec.Struct`

#### Scenario: Removed inclusion is query-owned

- **WHEN** `monitor.snapshot(include_removed=True)` runs
- **THEN** the same typed snapshot graph includes active and removed catalog environments without a second CLI catalog read or a CLI-specific snapshot type

#### Scenario: Watch is cancellable without leaks

- **WHEN** consumer cancels the task iterating `monitor.watch()` mid-iteration
- **THEN** `watch()` stops yielding, no background thread/process survives past consumer exit

#### Scenario: Interval floor enforced

- **WHEN** `monitor.watch(interval=0.05)` is called
- **THEN** `ValueError` is raised before any iteration

### Requirement: Snapshot top-level contract

`Snapshot.schema_version` MUST always be `3`. Version 3 is an additive migration from mandatory MYL-55 version 2: every `EnvironmentSnapshot` gains only required `pgadmin`; required `observed_port` and `artifacts`, all earlier fields, and all v2 collection/filter/removed-row meanings remain unchanged. `generated_at` MUST be tz-aware UTC. `projects` ordered by `project_id` ascending; `environments` ordered by `id` ascending. `GET /api/v1/snapshot` returns the default non-removed version-3 JSON (msgspec encode). `odcli env list --json` wraps the same `Snapshot` object in CLI envelope v1 `result`/`data` (`command="env.list"`); CLI envelope version remains `1` and is independent of snapshot schema version.

`project_id` filter: `None` MUST select all discovered projects; an opaque id matching a discovered project MUST select that project and its environments; an unknown id MUST return `projects == ()` and `environments == ()` without raising. With `include_removed=False`, a project exists only if it has at least one non-removed environment. With `include_removed=True`, a project containing only removed environments MUST appear with those rows, all from the single atomic catalog selection. `ProjectSummary.environment_count` MUST count the environments included in the returned snapshot; project counts and partial-result behavior remain unchanged. pgAdmin eligibility for an included removed row MUST be `environment_not_ready` without adding a database, port, health, or Docker probe.

#### Scenario: Full snapshot shape

- **WHEN** `monitor.snapshot()` runs with two projects each having environments
- **THEN** `schema_version==3`, `projects` contains two `ProjectSummary`, and `environments` contains all non-removed environments of both projects with MYL-55 v2 fields plus pgAdmin eligibility

#### Scenario: Default full snapshot shape

- **WHEN** `monitor.snapshot()` runs with two projects each having active environments
- **THEN** `schema_version==3`, `projects` contains two `ProjectSummary`, and `environments` contains all non-removed environments with version-2 fields plus pgAdmin eligibility

#### Scenario: Removed-only project is conditional

- **WHEN** one project has only removed environments
- **THEN** it is absent from `snapshot()` and present with those rows in `snapshot(include_removed=True)`; each included removed environment retains the v2 stopped/null runtime, artifact, and no-port-probe semantics and has `pgadmin.state=="environment_not_ready"`

#### Scenario: Removed environment keeps v2 behavior in v3

- **WHEN** `monitor.snapshot(include_removed=True)` includes a removed environment
- **THEN** its MYL-55 stopped/null runtime, artifact, and no-port-probe semantics are unchanged, `observed_port is None`, and `pgadmin.state=="environment_not_ready"`

#### Scenario: Project filter narrows result

- **WHEN** `monitor.snapshot(project_id="project_comerta_7e3d8a01")` runs
- **THEN** `projects` contains only the matching `ProjectSummary` and `environments` contains only that project's non-removed environments

#### Scenario: Unknown project filter returns empty

- **WHEN** `monitor.snapshot(project_id="project_unknown", include_removed=True)` runs
- **THEN** `projects == ()` and `environments == ()`, no exception

### Requirement: Project discovery from canonical repository provenance

Project identity SHALL be built from canonical repository provenance of the catalog environments selected for the query, **not** from the process registry and **not** from a filesystem scan.

- The source SHALL be one call to existing atomic `BackupCatalog.list_environments_with_runtimes(include_removed=...)`, extended with keyword-only `include_removed: bool = False`; environments and runtime identities SHALL be read within one SQLite transaction/snapshot.
- With `include_removed=False`, its environment query SHALL select only rows whose state is not `removed`.
- With `include_removed=True`, its environment query SHALL select active and removed rows; it SHALL NOT fall back to a separate `list_environments()` plus runtime read.
- Rows SHALL be grouped by `git_common_dir`, not display name or `repository_root`; a removed-only group SHALL exist only when removed rows are included.
- Monitor opaque `project_id` SHALL remain `"project_" + repo_key(repository_root, git_common_dir)`. Postgres compose identity SHALL keep the unprefixed `repo_key`; the collector SHALL call `PostgresCluster.from_project(repository_root)` and SHALL NOT pass the `project_` prefix into `compose_project_name`.
- `name` SHALL be `Path(repository_root).name`; `display_hint` SHALL be the `repo_key`. Global mode (`project_id=None`) SHALL NOT scan the filesystem for unregistered repositories.

For either selection, project cluster collection and partial-data isolation SHALL run once per included group. A missing repository/manifest or failed Git/storage/runtime component SHALL produce the existing typed null/error values without dropping another included environment or project. `ProjectSummary.environment_count` SHALL equal the number of selected rows in that group.

#### Scenario: Projects grouped by canonical provenance

- **WHEN** catalog contains environments from two repositories both named "odoo" but with different `git_common_dir`
- **THEN** two distinct `ProjectSummary` entries with different `project_id` and disambiguated `display_hint`

#### Scenario: Stopped environment still in project

- **WHEN** an environment has `state="ready"` but no running Odoo process
- **THEN** it still contributes to its project's `environment_count` and appears in `environments`

#### Scenario: Removed environment excluded

- **WHEN** an environment has `state="removed"` in catalog
- **THEN** it is excluded from `projects` and `environments` when `include_removed=False`

#### Scenario: Active-only default excludes removed rows and groups

- **WHEN** the catalog has one active project and another project containing only removed rows and `monitor.snapshot()` runs
- **THEN** discovery calls `list_environments_with_runtimes(include_removed=False)` exactly once inside one SQLite snapshot, the active project is grouped, and the removed-only project and its rows are absent

#### Scenario: Removed-inclusive selection groups all selected rows

- **WHEN** the same catalog is queried with `monitor.snapshot(include_removed=True)`
- **THEN** discovery calls `list_environments_with_runtimes(include_removed=True)` exactly once inside one SQLite snapshot, active and removed rows are grouped by canonical provenance, the removed-only project appears, and each project count matches its included rows

#### Scenario: Environment and runtime selection is atomic

- **WHEN** a concurrent catalog writer changes an environment runtime while `snapshot(include_removed=True)` begins planning
- **THEN** the selected environment rows and runtime identities come from the same SQLite read transaction and the monitor performs no second environment or runtime query

#### Scenario: Partial removed project remains representable

- **WHEN** an included removed environment points to an unreadable repository and has no live runtime record
- **THEN** its project and environment remain in the snapshot, `cluster` and unavailable Git/storage/runtime values follow the existing partial-result contract, and collection of other rows continues

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

Every environment selected by `include_removed` SHALL become one `EnvironmentSnapshot`. For active rows, `runtime.state` SHALL remain `stopped`, `ready`, or `not_ready` under the existing PID/create-time and bounded health-probe rules. For a removed row, the collector SHALL NOT probe a port or Odoo health endpoint and SHALL emit stopped/null live-runtime values while retaining catalog lifecycle identity and any safely obtainable Git, storage, and artifact data.

Runtime reconciliation for non-removed rows SHALL read catalog `environment_runtime`, verify both `psutil.pid_exists(pid)` and exact `psutil.Process(pid).create_time() == recorded_create_time`, and SHALL treat a missing/stale/reused PID as stopped without deleting catalog data. After a live match, readiness SHALL use one bounded `httpx.get(f"{http_url}/web/health?db_server_status=true", timeout=2.0)`; only HTTP 200 with JSON `status == "pass"` is ready, while other outcomes are not-ready with process metrics retained.

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

- **WHEN** an environment has a live matching process but `GET {http_url}/web/health?db_server_status=true` does not return HTTP 200 with JSON `status=="pass"` within 2.0s
- **THEN** `runtime.state == "not_ready"`, process metrics still populated

#### Scenario: Active stopped environment has null runtime metrics

- **WHEN** a selected non-removed environment has no current runtime record
- **THEN** `runtime.state == "stopped"`, `runtime.root_pid is None`, and `runtime.cpu_percent is None`

#### Scenario: Active live environment uses existing runtime semantics

- **WHEN** a selected non-removed environment has a matching live PID/create-time and its health probe succeeds
- **THEN** `runtime.state == "ready"` and verified process-tree metrics are populated; if that probe fails, state is `not_ready` and those metrics remain populated

#### Scenario: Removed environment is retained without live probes

- **WHEN** `monitor.snapshot(include_removed=True)` selects a row with `lifecycle_state="removed"`
- **THEN** exactly one snapshot row is returned with stopped/null live-runtime values, no address or health probe is made for it, and safely available Git/storage/artifact fields follow normal partial-data isolation

### Requirement: Odoo process tree metrics

`internal/process_metrics.py::collect_process_tree(...) -> ProcessTreeResult | None` is internal. Collector maps:

- `None` → `RuntimeMetrics(state=STOPPED, root_pid=None, child_pids=(), process_count=0, cpu_percent=None, rss_bytes=None, started_at=None, http_url=None, http_port=None, database_name=None, commit_sha=None, branch=None)`;
- live result + successful health GET → `state=READY` and copy `child_pids`/`process_count`/`cpu_percent`/`rss_bytes` plus identity fields from the runtime record;
- live result + failed health GET → `state=NOT_READY` with the same metrics.

`cpu_percent` MUST be `None` on the first sample for a `(pid, create_time)` pair; later `watch()` iterations MUST be numeric when the process is still live. Values are allowed to exceed 100.

Root `NoSuchProcess` / `AccessDenied` / `ZombieProcess` / PID reuse → `None` from `collect_process_tree` → `stopped`. Child-only `AccessDenied`: skip that child. Do not set `StorageFootprint.complete` from process errors.

#### Scenario: Aggregated CPU over tree

- **WHEN** a ready Odoo root PID 43120 has two workers 43131, 43132
- **THEN** `process_count == 3`, `child_pids == (43131, 43132)`, `cpu_percent` is the sum across the three PIDs

#### Scenario: First sample CPU is null

- **WHEN** a snapshot is the first after process start and no previous CPU point exists
- **THEN** `cpu_percent is None` (one-shot snapshot has no delta); a subsequent `watch()` iteration produces a numeric `cpu_percent`

#### Scenario: AccessDenied isolated

- **WHEN** `psutil.Process(43120)` raises `AccessDenied` for one environment's root PID
- **THEN** that environment's `runtime.state == "stopped"`, resource fields are null, other environments are unaffected

### Requirement: Git activity relative to default branch

`GitActivity` fields are those in Canonical snapshot types. `default_branch` MUST always be `"main"`. Do not read a manifest field and do not guess from remotes.

Rules:
- default tip: `git rev-parse --verify main@{upstream}` if exit 0, else `git rev-parse --verify refs/heads/main` (timeout 10s per git call);
- line totals are committed three-dot `git diff --numstat <merge-base>...HEAD`, not uncommitted `HEAD±`;
- binary files (`-` in numstat) contribute 0;
- no-common-ancestor → `state="orphan"`, `ahead=behind=diff=None`;
- any other Git CLI failure → `GitActivity(default_branch="main", head_sha=None, short_sha=None, branch="unknown", ahead=None, behind=None, diff=None, state=ORPHAN)`;
- cache key `(worktree_path, HEAD SHA, default-branch SHA)`, TTL 15s.

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

`StorageFootprint` / `PythonEnvFootprint` / `DatabaseFootprint` fields are those in Canonical snapshot types. `database` is always a `DatabaseFootprint` object (not `None`): `shared` → `owned=False`, byte fields `None`.

Directory size: `du -sb <path>` iff `shutil.which("du")` and subprocess exit 0 with a parseable integer (timeout 10s); else `os.walk(followlinks=False)` + `Path.stat().st_size`, skip symlink dirs, dedup by `Path.resolve()`.

Owned copy DB size: `internal/postgres_size.py::database_size_bytes` via `psql -c "SELECT pg_database_size('...')"` (same subprocess pattern as `_verify_database_via_psql`; never Odoo HTTP, never `DatabaseResource` public API, never psycopg). Connection params from the environment generated `odoo.conf` via `StartConfig.from_odoo_config`. Filestore: `validate_filestore_containment` then directory size.

Shared/source database, external venv, shared Git object store and shared cluster volume are excluded. Cluster volume lives only on `ClusterSnapshot.metrics.volume_usage_bytes`. Owned-component failure → that field `None` and `complete=False`. Cache key `environment_id`, TTL 15s.

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

`ClusterSnapshot` / `ClusterContainer` / `ClusterMetrics` / `ClusterEndpoint` / `ClusterResourceSnapshot` MUST expose exactly the fields in Canonical snapshot types. `pid_scope` MUST be `PidScope` StrEnum (not a bare Literal).

Compose container is resolved via `PostgresCluster.from_project(repository_root)` → compose project `odcli_pg_{repo_key}` + service `postgres`, then batch `docker inspect` / `docker stats --no-stream`. External: `resource_snapshot()` returns `None`; collector still emits a `ClusterSnapshot` with `mode="external"`, `owned=False`, `container=None`, `metrics=None`, `unavailability_reason="external_not_owned"`.
- Stopped vs missing compose: `unavailability_reason="stopped"` iff `PostgresCluster.status() == STOPPED`. `unavailability_reason="missing"` iff `status()` is not `STOPPED` and the `postgres` service container ID cannot be resolved after `compose ps`. Never emit both. `sampled_at`: one tz-aware UTC `datetime.now(UTC)` at collection; copy the same value into `ClusterMetrics.sampled_at`, `ClusterResourceSnapshot.sampled_at`, and `ClusterSnapshot.sampled_at`. If `metrics is None`, all three `sampled_at` are `None`.
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

- Ошибка одного environment (Git/storage/psutil/DB size) → that environment stays in the snapshot with nested partials (`git.state=orphan`, `storage.complete=False`, `runtime.state=stopped`); no environment-level `error` field. Other environments continue.
- Ошибка одного cluster (Docker inspect/stats) → affected cluster получает `unavailability_reason`, остальные продолжаются.
- Ошибка project manifest load → `cluster=None` для этого project, environments продолжаются.
- Catalog SQLite error → snapshot fails целиком с typed `MonitorError` (это единственная unrecoverable ошибка — без catalog нет project discovery); collector не сваливается в generic `Exception`.
- `psutil` import error (missing extra) on first `snapshot()` (default process provider) → `MonitorExtrasMissingError` with `pip install odoo-instance-sdk[metrics]`. Construction of `EnvironmentMonitor()` succeeds without importing psutil.
- Docker CLI missing → только affected compose clusters; не global crash (covered above).

Типизированные ошибки в `exceptions.py` (наследники `OdooInstanceSdkError`): `MonitorError` (base), `MonitorExtrasMissingError`. Сообщения redacted (без secrets/absolute paths). Component failures изолируются в snapshot (`complete=False`/`unavailability_reason`), не отдельным exception; catalog SQLite error → `MonitorError`.

#### Scenario: One environment failure isolated

- **WHEN** Git CLI fails for one environment's worktree
- **THEN** that environment's `git.state == "orphan"`, `ahead`/`behind`/`diff`/`head_sha` are None, `branch == "unknown"`; other environments are unaffected

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

Snapshot models MUST быть frozen `msgspec.Struct(frozen=True, forbid_unknown_fields=True, kw_only=True)` — reusing принятый в SDK `msgspec`, не дублируются Pydantic DTO. FastAPI использует `msgspec.json.encode(snapshot)` only; Pydantic не добавляется как dependency. Все enums (`RuntimeState`, `GitActivityState`) — `enum.StrEnum`.

Приложение может использовать collector внутри своего FastAPI/Flask/worker process без зависимости от встроенного FastAPI backend (extra `metrics` достаточно для SDK; `dashboard` только для built-in server).

#### Scenario: Models are msgspec Structs

- **WHEN** source is inspected for `Snapshot`/`EnvironmentSnapshot`/`ClusterSnapshot`
- **THEN** all are `msgspec.Struct` subclasses with `frozen=True, forbid_unknown_fields=True, kw_only=True`

#### Scenario: FastAPI without Pydantic

- **WHEN** the built-in FastAPI server serialises a snapshot
- **THEN** it uses `msgspec.json.encode`, not Pydantic; `pydantic` is not a runtime dependency

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

### Requirement: Canonical artifact and port reconciliation

For every included catalog environment, `EnvironmentMonitor` SHALL compute artifact availability and port observation during the same snapshot collection that computes runtime, Git, storage, and cluster data. It SHALL use the already-open canonical catalog for environment and backup identity, existing Git worktree helpers for registration, safe filesystem predicates for paths, and existing `probe_address` behavior for the allocated interface/port. Transport adapters SHALL NOT repeat these probes.

`EnvironmentArtifacts.backup_exists` SHALL be `None` when the environment has no associated backup id. Otherwise it SHALL be `True` only when the referenced catalog row has `state=available` and its recorded file exists. A referenced `failed` or `deleted` row, missing catalog row, or missing recorded file SHALL yield `False`. `python_contained` SHALL be `True` for a reused/external interpreter and otherwise report whether the owned Python environment is contained by its environment artifact root. A component failure SHALL yield the conservative unavailable value for that component without failing unrelated environments.

`observed_port` SHALL be `None` unless the environment lifecycle is `ready`, its runtime is live (`ready` or `not_ready`), and an allocated HTTP port is known. Otherwise it SHALL be `free`, `occupied`, or `unknown` from the existing bounded address probe. Observation SHALL be read-only and SHALL NOT perform an HTTP health request or change the runtime state.

#### Scenario: Artifact availability is collected once

- **WHEN** a ready environment has a registered worktree, generated config, Python interpreter, dependency lock, and an associated backup file
- **THEN** its `artifacts` fields are true and CLI renderers can display `ARTIFACTS=ok` without opening the catalog or probing the filesystem

#### Scenario: Missing backup is explicit

- **WHEN** an environment references a backup whose catalog row or recorded file is missing
- **THEN** `artifacts.backup_exists` is `False` and collection of other environments continues

#### Scenario: Non-available backup is not an available artifact

- **WHEN** an environment references a catalog backup in `failed` or `deleted` state even if its recorded path exists
- **THEN** `artifacts.backup_exists` is `False`, matching the existing `list_backups()`-based human output semantics

#### Scenario: Live allocated port is observed

- **WHEN** a ready environment has a live runtime and a known allocated port that cannot be bound
- **THEN** `observed_port` is `occupied` without changing runtime state or sending an HTTP request

#### Scenario: Removed environment is not port-probed

- **WHEN** an included environment has lifecycle state `removed`
- **THEN** `observed_port` is `None`
