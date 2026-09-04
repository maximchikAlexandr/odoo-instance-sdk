## ADDED Requirements

### Requirement: Project-only monitoring plans

Monitor planning SHALL include initialized projects from canonical project registration even when they have no environment catalogue rows. For each live project-owned runtime it SHALL validate stale-process identity and collect the same PID, worker PID, process count, CPU, RAM, readiness, URL, database, and applicable PostgreSQL cluster metrics used for environment-owned runtimes. Project filtering and deterministic ordering SHALL include both ownership kinds without creating synthetic environments.

#### Scenario: Initialized project without environments is visible
- **WHEN** the catalogue contains an initialized project and no environments
- **THEN** the snapshot includes the project and an empty environment list rather than returning an empty project list

#### Scenario: Live project runtime has metrics
- **WHEN** that project has a valid live runtime identity
- **THEN** its runtime/process/readiness/database fields and applicable cluster metrics appear in the typed snapshot

#### Scenario: Stale project PID is not reused
- **WHEN** the stored project PID exists but its create time does not match
- **THEN** it is reported as stale/stopped under the existing identity rules and unrelated process metrics are not exposed

### Requirement: Snapshot preserves environment compatibility

The snapshot SHALL represent project-owned runtimes additively while preserving existing environment arrays, environment runtime states, filtering, redaction, JSON serialization, and cache boundaries. Project runtime collection SHALL reuse the existing typed collector and process provider rather than adding a second monitor implementation.

#### Scenario: Mixed ownership snapshot
- **WHEN** one project-owned runtime and existing environment-owned runtimes are live
- **THEN** all are represented deterministically and current environment consumers retain their existing fields

## MODIFIED Requirements

### Requirement: Canonical snapshot types

Public snapshot types SHALL remain `msgspec.Struct(frozen=True, forbid_unknown_fields=True, kw_only=True)` except StrEnums, and `ProcessTreeResult` SHALL remain private. The complete public type inventory and fields SHALL be:

```python
class RuntimeState(enum.StrEnum):
    STOPPED = "stopped"; READY = "ready"; NOT_READY = "not_ready"
class GitActivityState(enum.StrEnum):
    CLEAN = "clean"; AHEAD = "ahead"; BEHIND = "behind"; DIVERGED = "diverged"; ORPHAN = "orphan"
class PidScope(enum.StrEnum):
    HOST = "host"; DOCKER_VM = "docker_vm"; UNAVAILABLE = "unavailable"
class PortObservation(enum.StrEnum):
    FREE = "free"; OCCUPIED = "occupied"; UNKNOWN = "unknown"
class PgAdminEligibilityState(enum.StrEnum):
    ELIGIBLE = "eligible"; ENVIRONMENT_NOT_READY = "environment_not_ready"
    DATABASE_UNRESOLVED = "database_unresolved"; CLUSTER_NOT_OWNED = "cluster_not_owned"
    CLUSTER_UNHEALTHY = "cluster_unhealthy"
class PgAdminEligibility:
    state: PgAdminEligibilityState
class GitDiff:
    added: int; deleted: int
class GitActivity:
    default_branch: str; head_sha: str | None; short_sha: str | None; branch: str
    ahead: int | None; behind: int | None; diff: GitDiff | None; state: GitActivityState
class PythonEnvFootprint:
    owned: bool; bytes: int | None
class DatabaseFootprint:
    owned: bool; postgres_bytes: int | None; filestore_bytes: int | None; total_bytes: int | None
class StorageFootprint:
    total_bytes: int; complete: bool; worktree_bytes: int | None
    python_environment: PythonEnvFootprint; database: DatabaseFootprint; other_files_bytes: int | None
class RuntimeMetrics:
    state: RuntimeState; root_pid: int | None; child_pids: tuple[int, ...]; process_count: int
    cpu_percent: float | None; rss_bytes: int | None; started_at: datetime | None
    http_url: str | None; http_port: int | None; database_name: str | None
    commit_sha: str | None; branch: str | None
class ClusterContainer:
    id: str | None; name: str | None; image: str | None; pid: int | None; pid_scope: PidScope
class ClusterMetrics:
    cpu_percent: float | None; memory_usage_bytes: int | None; memory_limit_bytes: int | None
    volume_usage_bytes: int | None; sampled_at: datetime | None
class ClusterEndpoint:
    host: str; port: int
class ClusterResourceSnapshot:
    container: ClusterContainer | None; metrics: ClusterMetrics | None
    unavailability_reason: str | None; sampled_at: datetime | None
class ClusterSnapshot:
    mode: Literal["external", "compose"]; owned: bool; state: PostgresClusterState
    endpoint: ClusterEndpoint | None; container: ClusterContainer | None; metrics: ClusterMetrics | None
    unavailability_reason: str | None; sampled_at: datetime | None
class EnvironmentArtifacts:
    worktree_exists: bool; worktree_registered: bool; config_exists: bool; python_exists: bool
    python_contained: bool; dependency_lock_exists: bool; backup_exists: bool | None
class EnvironmentSnapshot:
    id: str; project_id: str; name: str; branch: str; short_sha: str | None
    db_mode: Literal["shared", "copy"]; database: str | None; lifecycle_state: EnvironmentState
    allocated_http_port: int | None; observed_port: PortObservation | None
    artifacts: EnvironmentArtifacts; runtime: RuntimeMetrics; git: GitActivity
    storage: StorageFootprint; pgadmin: PgAdminEligibility
class ProjectSummary:
    id: str; name: str; display_hint: str; environment_count: int
    cluster: ClusterSnapshot | None; runtime: RuntimeMetrics | None
class Snapshot:
    schema_version: int; generated_at: datetime; projects: tuple[ProjectSummary, ...]
    environments: tuple[EnvironmentSnapshot, ...]
```

All canonical collection meanings remain unchanged. `ProjectSummary.runtime` SHALL be `None` when no project-owned runtime record exists. A recorded but non-live or stale project runtime SHALL be a present `RuntimeMetrics` with `state="stopped"`, `root_pid=None`, `child_pids=()`, `process_count=0`, and all CPU/RAM/start/HTTP/database/commit/branch fields null; unrelated process data SHALL never be reused. A live record SHALL use the same ready/not-ready, process-tree, URL, database, revision, and redaction rules as environment runtime collection. No other public field SHALL be added.

#### Scenario: Project runtime null and stopped states differ
- **WHEN** one registered project has no runtime record and another has a stale project-owned record
- **THEN** the first has `runtime=None` and the second has the canonical present stopped/null `RuntimeMetrics`

### Requirement: Snapshot top-level contract

`Snapshot.schema_version` SHALL always be `4`. Version 4 SHALL be an additive migration from version 3 adding only required `ProjectSummary.runtime: RuntimeMetrics | None`; every environment field including `pgadmin`, all v3 collection/partial-result/removed-row meanings, CLI envelope version 1, timezone-aware UTC `generated_at`, and msgspec encoding SHALL remain unchanged. Projects SHALL be ordered by `id` ascending and environments by `id` ascending. Registered project-only rows SHALL participate in this ordering and SHALL be returned even with zero environments. `project_id=None` SHALL select all registered/discovered projects; a matching opaque ID SHALL select that project and its environments; unknown IDs SHALL return empty tuples. `include_removed` SHALL continue to govern only environment rows and SHALL not hide a registered project-only row. `environment_count` SHALL count returned environments only.

The existing atomic selection, partial-result warnings, cache key/TTL/single-flight behavior, secret redaction, and JSON/TOON semantics SHALL apply unchanged to version 4 and include project runtime state. Cache keys SHALL continue to include filter and `include_removed`; a project-runtime change SHALL become visible after normal invalidation/TTL under the same snapshot consistency boundary. `GET /api/v1/snapshot`, generated OpenAPI, generated TypeScript client, and dashboard SHALL consume version 4 and the canonical nullable field without a parallel DTO or endpoint.

#### Scenario: Version 3 migrates additively to version 4
- **WHEN** a frozen version-3 fixture is migrated/collected with no project-owned runtime
- **THEN** the version is 4, every prior field and ordering is unchanged, and each project adds only `runtime=null`

#### Scenario: API and clients enforce version 4
- **WHEN** live, stopped, null, mixed-owner, filtered, cached, or redacted snapshots pass through API/OpenAPI/client/dashboard contract tests
- **THEN** they validate as schema version 4 with identical canonical runtime semantics and no secret-bearing or unknown fields
