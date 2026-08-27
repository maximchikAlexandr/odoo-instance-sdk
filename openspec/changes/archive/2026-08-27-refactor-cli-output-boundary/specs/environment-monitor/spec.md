## ADDED Requirements

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

## MODIFIED Requirements

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
- `observed_port` / `artifacts` — as defined by canonical artifact and port reconciliation;
- `runtime` / `git` / `storage` — as their own requirements.

UI lifecycle badge uses `lifecycle_state`. UI/CLI port uses `runtime.http_port` when `runtime.state` is `ready` or `not_ready`, else `allocated_http_port`.

#### Scenario: EnvironmentSnapshot mapping

- **WHEN** a copy-mode catalog row has `branch="feat/x"`, generated config `http_port=8070`, `target_db_name="db_x"`, and git `head_sha` starting `abc1234def`
- **THEN** `branch=="feat/x"`, `allocated_http_port==8070`, `database=="db_x"`, `short_sha=="abc1234"`, `lifecycle_state` equals catalog `state`, and typed `observed_port`/`artifacts` are present

#### Scenario: EnvironmentSnapshot carries project_id

- **WHEN** `monitor.snapshot()` returns an environment
- **THEN** `environment.project_id` equals the owning `ProjectSummary.id`

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

`Snapshot.schema_version` MUST always be `2`. Version 2 is an additive schema migration from version 1: every `EnvironmentSnapshot` gains required `observed_port` and `artifacts` fields; all version-1 fields and their meanings remain unchanged. `generated_at` MUST be tz-aware UTC. `projects` ordered by `project_id` ascending; `environments` ordered by `id` ascending. `GET /api/v1/snapshot` returns the default non-removed version-2 JSON (msgspec encode). `odcli env list --json` wraps the same default `Snapshot` object in CLI envelope v1 `result`/`data` (`command="env.list"`); CLI envelope version remains `1` and is independent of snapshot schema version.

`project_id` filter: `None` → all discovered projects; opaque id matching a discovered project → that project + its environments; unknown id → `projects == ()` and `environments == ()`, no exception. With `include_removed=False`, a project exists only if it has at least one non-removed environment. With `include_removed=True`, a project containing only removed environments SHALL appear. `ProjectSummary.environment_count` SHALL count environments included in that returned snapshot.

#### Scenario: Full snapshot shape

- **WHEN** `monitor.snapshot()` runs with two projects each having environments
- **THEN** `projects` contains two `ProjectSummary`, `environments` contains all non-removed environments of both projects

#### Scenario: Default full snapshot shape

- **WHEN** `monitor.snapshot()` runs with two projects each having active environments
- **THEN** `schema_version==2`, `projects` contains two `ProjectSummary`, and `environments` contains all non-removed environments with version-2 fields

#### Scenario: Removed-only project is conditional

- **WHEN** one project has only removed environments
- **THEN** it is absent from `snapshot()` and present with those rows in `snapshot(include_removed=True)`

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
