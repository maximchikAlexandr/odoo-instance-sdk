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
